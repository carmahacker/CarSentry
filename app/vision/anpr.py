import os
import re
from collections import Counter

import cv2
import numpy as np
import torch
import torch.nn as nn
from torchvision import transforms
from ultralytics import YOLO

import torch.ao.quantization.quantize_fx as quantize_fx
from torch.ao.quantization import QConfigMapping


# ============================================================
# CONFIG
# ============================================================

ANPR_YOLO_MODEL = os.getenv(
    "ANPR_YOLO_MODEL",
    "/models/anpr/yolo/best.pt",
)

ANPR_OCR_MODEL = os.getenv(
    "ANPR_OCR_MODEL",
    "/models/anpr/ocr/crnn_ocr_model_int8_fx.pth",
)

ANPR_CONFIDENCE = float(
    os.getenv(
        "ANPR_CONFIDENCE",
        "0.35",
    )
)

ANPR_OCR_DEVICE = os.getenv(
    "ANPR_OCR_DEVICE",
    "cpu",
)


#
# Количество кадров, которое должно
# использоваться для temporal voting.
#
# Сейчас ANPRDetector получает один frame,
# поэтому непосредственное temporal voting
# будет подключено уровнем выше:
#
# plate_detector -> pipeline -> frame_provider.
#
# Значение уже доступно из этого модуля.
#
ANPR_VOTE_FRAMES = max(
    1,
    int(
        os.getenv(
            "ANPR_VOTE_FRAMES",
            "3",
        )
    ),
)


OCR_IMG_HEIGHT = 32
OCR_IMG_WIDTH = 128


#
# Алфавит оригинальной OCR-модели.
#
OCR_ALPHABET = (
    "0123456789"
    "ABCEHKMOPTXY"
)


# ============================================================
# CRNN
# ============================================================

class CRNN(nn.Module):

    def __init__(
        self,
        num_classes,
    ):
        super().__init__()

        self.cnn = nn.Sequential(

            nn.Conv2d(
                1,
                64,
                kernel_size=3,
                padding=1,
            ),

            nn.ReLU(True),

            nn.MaxPool2d(
                2,
                2,
            ),

            nn.Conv2d(
                64,
                128,
                kernel_size=3,
                padding=1,
            ),

            nn.ReLU(True),

            nn.MaxPool2d(
                2,
                2,
            ),

            nn.Conv2d(
                128,
                256,
                kernel_size=3,
                padding=1,
            ),

            nn.BatchNorm2d(
                256
            ),

            nn.ReLU(True),

            nn.Conv2d(
                256,
                256,
                kernel_size=3,
                padding=1,
            ),

            nn.ReLU(True),

            nn.MaxPool2d(
                (2, 1),
                (2, 1),
            ),

            nn.Conv2d(
                256,
                512,
                kernel_size=3,
                padding=1,
            ),

            nn.BatchNorm2d(
                512
            ),

            nn.ReLU(True),

            nn.Conv2d(
                512,
                512,
                kernel_size=3,
                padding=1,
            ),

            nn.ReLU(True),

            nn.MaxPool2d(
                (2, 1),
                (2, 1),
            ),
        )

        self.rnn = nn.LSTM(
            512 * 2,
            256,
            bidirectional=True,
            num_layers=2,
            batch_first=True,
        )

        self.classifier = nn.Linear(
            512,
            num_classes,
        )


    def forward(
        self,
        x,
    ):
        x = self.cnn(
            x
        )

        (
            batch,
            channels,
            height,
            width,
        ) = x.size()

        x = x.reshape(
            batch,
            channels * height,
            width,
        )

        x = x.permute(
            0,
            2,
            1,
        )

        x, _ = self.rnn(
            x
        )

        x = self.classifier(
            x
        )

        x = x.permute(
            1,
            0,
            2,
        )

        return (
            nn.functional
            .log_softmax(
                x,
                dim=2,
            )
        )


# ============================================================
# OCR
# ============================================================

class CRNNRecognizer:

    def __init__(
        self,
        model_path,
        device="cpu",
    ):
        self.device = torch.device(
            device
        )

        self.transform = (
            transforms.Compose([
                transforms.ToPILImage(),

                transforms.Grayscale(),

                transforms.Resize(
                    (
                        OCR_IMG_HEIGHT,
                        OCR_IMG_WIDTH,
                    )
                ),

                transforms.ToTensor(),

                transforms.Normalize(
                    mean=[0.5],
                    std=[0.5],
                ),
            ])
        )

        self.int_to_char = {
            i + 1: char
            for i, char
            in enumerate(
                OCR_ALPHABET
            )
        }

        #
        # CTC blank.
        #
        self.int_to_char[0] = ""

        num_classes = (
            len(
                OCR_ALPHABET
            )
            + 1
        )

        print(
            "Loading ANPR OCR:"
            f" {model_path}",
            flush=True,
        )

        model_to_load = (
            CRNN(
                num_classes
            )
            .eval()
        )

        qconfig_mapping = (
            QConfigMapping()
            .set_global(
                torch.ao.quantization
                .get_default_qconfig(
                    "fbgemm"
                )
            )
        )

        example_inputs = (
            torch.randn(
                1,
                1,
                OCR_IMG_HEIGHT,
                OCR_IMG_WIDTH,
            ),
        )

        model_prepared = (
            quantize_fx.prepare_fx(
                model_to_load,
                qconfig_mapping,
                example_inputs,
            )
        )

        model_quantized = (
            quantize_fx.convert_fx(
                model_prepared
            )
        )

        state_dict = torch.load(
            model_path,
            map_location=
                self.device,
        )

        model_quantized.load_state_dict(
            state_dict
        )

        self.model = (
            model_quantized
        )

        self.model.eval()

        print(
            "ANPR OCR loaded.",
            flush=True,
        )


    @torch.no_grad()
    def recognize(
        self,
        plate_image,
    ):
        if (
            plate_image is None
            or plate_image.size == 0
        ):
            return ""

        image = self.transform(
            plate_image
        )

        image = image.unsqueeze(
            0
        )

        image = image.to(
            self.device
        )

        predictions = self.model(
            image
        )

        return self._decode(
            predictions
        )


    def _decode(
        self,
        predictions,
    ):
        predictions = (
            predictions
            .permute(
                1,
                0,
                2,
            )
            .argmax(
                dim=2
            )[0]
        )

        decoded = []

        last_char_idx = 0

        for char_idx in predictions:

            char_idx = (
                char_idx.item()
            )

            if (
                char_idx != 0
                and char_idx
                != last_char_idx
            ):
                decoded.append(
                    self.int_to_char
                    .get(
                        char_idx,
                        "",
                    )
                )

            last_char_idx = (
                char_idx
            )

        return "".join(
            decoded
        )


# ============================================================
# ANPR DETECTOR
# ============================================================

class ANPRDetector:

    def __init__(
        self,
    ):
        print(
            "Loading ANPR plate detector:"
            f" {ANPR_YOLO_MODEL}",
            flush=True,
        )

        self.detector = YOLO(
            ANPR_YOLO_MODEL
        )

        print(
            "Loading ANPR OCR:"
            f" {ANPR_OCR_MODEL}",
            flush=True,
        )

        self.ocr = CRNNRecognizer(
            ANPR_OCR_MODEL,
            ANPR_OCR_DEVICE,
        )

        print(
            "ANPR detector + OCR loaded."
            f" vote_frames="
            f"{ANPR_VOTE_FRAMES}",
            flush=True,
        )


    # ========================================================
    # PERSPECTIVE
    # ========================================================

    def _order_points(
        self,
        pts,
    ):
        rect = np.zeros(
            (4, 2),
            dtype="float32",
        )

        s = pts.sum(
            axis=1
        )

        rect[0] = pts[
            np.argmin(s)
        ]

        rect[2] = pts[
            np.argmax(s)
        ]

        diff = np.diff(
            pts,
            axis=1,
        )

        rect[1] = pts[
            np.argmin(diff)
        ]

        rect[3] = pts[
            np.argmax(diff)
        ]

        return rect


    def _four_point_transform(
        self,
        image,
        pts,
    ):
        rect = (
            self._order_points(
                pts
            )
        )

        (
            tl,
            tr,
            br,
            bl,
        ) = rect

        width_a = np.sqrt(
            (
                (br[0] - bl[0]) ** 2
            )
            + (
                (br[1] - bl[1]) ** 2
            )
        )

        width_b = np.sqrt(
            (
                (tr[0] - tl[0]) ** 2
            )
            + (
                (tr[1] - tl[1]) ** 2
            )
        )

        max_width = max(
            int(width_a),
            int(width_b),
        )

        height_a = np.sqrt(
            (
                (tr[0] - br[0]) ** 2
            )
            + (
                (tr[1] - br[1]) ** 2
            )
        )

        height_b = np.sqrt(
            (
                (tl[0] - bl[0]) ** 2
            )
            + (
                (tl[1] - bl[1]) ** 2
            )
        )

        max_height = max(
            int(height_a),
            int(height_b),
        )

        if (
            max_width <= 0
            or max_height <= 0
        ):
            return image

        dst = np.array(
            [
                [
                    0,
                    0,
                ],

                [
                    max_width - 1,
                    0,
                ],

                [
                    max_width - 1,
                    max_height - 1,
                ],

                [
                    0,
                    max_height - 1,
                ],
            ],
            dtype="float32",
        )

        matrix = (
            cv2.getPerspectiveTransform(
                rect,
                dst,
            )
        )

        return cv2.warpPerspective(
            image,
            matrix,
            (
                max_width,
                max_height,
            ),
        )


    # ========================================================
    # PERSPECTIVE PREPROCESS
    # ========================================================

    def _preprocess_plate(
        self,
        plate_image,
    ):
        if (
            plate_image is None
            or plate_image.size == 0
        ):
            return plate_image

        try:
            gray = cv2.cvtColor(
                plate_image,
                cv2.COLOR_BGR2GRAY,
            )

            blurred = cv2.GaussianBlur(
                gray,
                (5, 5),
                0,
            )

            _, threshold = (
                cv2.threshold(
                    blurred,
                    0,
                    255,
                    cv2.THRESH_BINARY
                    + cv2.THRESH_OTSU,
                )
            )

            contours, _ = (
                cv2.findContours(
                    threshold.copy(),
                    cv2.RETR_EXTERNAL,
                    cv2.CHAIN_APPROX_SIMPLE,
                )
            )

            if not contours:
                return plate_image

            contours = sorted(
                contours,
                key=cv2.contourArea,
                reverse=True,
            )

            for contour in contours:

                perimeter = (
                    cv2.arcLength(
                        contour,
                        True,
                    )
                )

                approximation = (
                    cv2.approxPolyDP(
                        contour,
                        0.02
                        * perimeter,
                        True,
                    )
                )

                if (
                    len(
                        approximation
                    )
                    == 4
                ):
                    return (
                        self
                        ._four_point_transform(
                            plate_image,
                            approximation
                            .reshape(
                                4,
                                2,
                            ),
                        )
                    )

        except Exception as exc:
            print(
                "[ANPR] perspective "
                f"error: {exc}",
                flush=True,
            )

        return plate_image


    # ========================================================
    # OCR TEXT NORMALIZATION
    # ========================================================

    def _normalize_plate_text(
        self,
        text,
    ):
        if not text:
            return ""

        text = str(
            text
        ).upper()

        allowed = set(
            OCR_ALPHABET
        )

        return "".join(
            char
            for char in text
            if char in allowed
        )


    # ========================================================
    # PLATE FORMAT SCORE
    # ========================================================

    def _plate_format_score(
        self,
        text,
    ):
        if not text:
            return 0.0

        score = 0.0

        #
        # A123BC77
        # A123BC177
        #
        if re.fullmatch(
            (
                r"[ABCEHKMOPTXY]"
                r"\d{3}"
                r"[ABCEHKMOPTXY]{2}"
                r"\d{2,3}"
            ),
            text,
        ):
            score += 1.0

        if (
            6
            <= len(text)
            <= 9
        ):
            score += 0.25

        if (
            len(text)
            < 5
        ):
            score -= 0.5

        return score


    # ========================================================
    # OCR VARIANTS
    # ========================================================

    def _ocr_variants(
        self,
        plate_image,
    ):
        if (
            plate_image is None
            or plate_image.size == 0
        ):
            return []

        variants = []


        def add_variant(
            name,
            image,
        ):
            if (
                image is None
                or image.size == 0
            ):
                return

            variants.append(
                (
                    name,
                    image,
                )
            )


        #
        # 1. Original.
        #
        original = (
            plate_image.copy()
        )

        add_variant(
            "original",
            original,
        )


        #
        # 2. Perspective correction.
        #
        try:
            perspective = (
                self._preprocess_plate(
                    original.copy()
                )
            )

        except Exception as exc:
            print(
                "[ANPR] perspective "
                f"variant failed: {exc}",
                flush=True,
            )

            perspective = (
                original.copy()
            )

        add_variant(
            "perspective",
            perspective,
        )


        source = perspective


        if (
            len(
                source.shape
            )
            == 2
        ):
            gray = (
                source.copy()
            )

        else:
            gray = cv2.cvtColor(
                source,
                cv2.COLOR_BGR2GRAY,
            )


        #
        # 3. Grayscale.
        #
        add_variant(
            "gray",
            gray,
        )


        #
        # 4. CLAHE.
        #
        try:
            clahe = (
                cv2.createCLAHE(
                    clipLimit=2.0,
                    tileGridSize=(
                        8,
                        8,
                    ),
                )
            )

            clahe_image = (
                clahe.apply(
                    gray
                )
            )

            add_variant(
                "clahe",
                clahe_image,
            )

        except Exception as exc:
            print(
                "[ANPR] CLAHE "
                f"variant failed: {exc}",
                flush=True,
            )


        #
        # 5. Otsu.
        #
        otsu = None

        try:
            blurred = (
                cv2.GaussianBlur(
                    gray,
                    (3, 3),
                    0,
                )
            )

            _, otsu = (
                cv2.threshold(
                    blurred,
                    0,
                    255,
                    cv2.THRESH_BINARY
                    + cv2.THRESH_OTSU,
                )
            )

            add_variant(
                "otsu",
                otsu,
            )

        except Exception as exc:
            print(
                "[ANPR] Otsu "
                f"variant failed: {exc}",
                flush=True,
            )


        #
        # 6. Inverted Otsu.
        #
        if otsu is not None:
            try:
                add_variant(
                    "otsu_inverted",
                    cv2.bitwise_not(
                        otsu
                    ),
                )

            except Exception as exc:
                print(
                    "[ANPR] inverted Otsu "
                    f"failed: {exc}",
                    flush=True,
                )


        #
        # 7. Adaptive threshold.
        #
        try:
            adaptive = (
                cv2.adaptiveThreshold(
                    gray,
                    255,
                    cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                    cv2.THRESH_BINARY,
                    31,
                    7,
                )
            )

            add_variant(
                "adaptive",
                adaptive,
            )

        except Exception as exc:
            print(
                "[ANPR] adaptive "
                f"variant failed: {exc}",
                flush=True,
            )


        #
        # 8. Sharpened.
        #
        try:
            blurred = (
                cv2.GaussianBlur(
                    gray,
                    (0, 0),
                    3,
                )
            )

            sharpened = (
                cv2.addWeighted(
                    gray,
                    1.8,
                    blurred,
                    -0.8,
                    0,
                )
            )

            add_variant(
                "sharpened",
                sharpened,
            )

        except Exception as exc:
            print(
                "[ANPR] sharpen "
                f"variant failed: {exc}",
                flush=True,
            )


        #
        # 9. Upscaled.
        #
        try:
            upscaled = cv2.resize(
                gray,
                None,
                fx=2.0,
                fy=2.0,
                interpolation=
                    cv2.INTER_CUBIC,
            )

            add_variant(
                "upscaled",
                upscaled,
            )

        except Exception as exc:
            print(
                "[ANPR] upscale "
                f"variant failed: {exc}",
                flush=True,
            )


        return variants


    # ========================================================
    # MULTI-PASS OCR
    # ========================================================

    def _recognize_plate_multi(
        self,
        plate_image,
    ):
        variants = (
            self._ocr_variants(
                plate_image
            )
        )

        candidates = []


        for (
            variant_name,
            variant_image,
        ) in variants:

            try:
                raw_text = (
                    self.ocr.recognize(
                        variant_image
                    )
                )

            except Exception as exc:
                print(
                    "[ANPR] OCR "
                    f"{variant_name} "
                    f"failed: {exc}",
                    flush=True,
                )

                continue


            text = (
                self
                ._normalize_plate_text(
                    raw_text
                )
            )


            if not text:
                continue


            candidates.append({
                "variant":
                    variant_name,

                "text":
                    text,
            })


        if not candidates:
            return {
                "text":
                    None,

                "confidence":
                    0.0,

                "votes":
                    0,

                "total":
                    0,

                "candidates":
                    [],
            }


        counter = Counter(
            item["text"]
            for item
            in candidates
        )


        total_votes = len(
            candidates
        )


        #
        # Основной критерий:
        # количество одинаковых результатов.
        #
        # Формат номера используется только
        # как tie-break, но НЕ увеличивает
        # ocr_confidence.
        #
        ranked = sorted(
            counter.items(),

            key=lambda item: (
                item[1],

                self._plate_format_score(
                    item[0]
                ),

                len(
                    item[0]
                ),
            ),

            reverse=True,
        )


        (
            best_text,
            best_votes,
        ) = ranked[0]


        #
        # Реальный confidence =
        # доля preprocessing-вариантов,
        # которые согласились.
        #
        # 8 / 9 = 0.888888...
        #
        ocr_confidence = (
            best_votes
            / total_votes
        )


        print(
            "[ANPR] OCR voting "
            f"best={best_text} "
            f"votes={best_votes}/"
            f"{total_votes} "
            f"confidence="
            f"{ocr_confidence:.2f} "
            f"candidates="
            f"{dict(counter)}",
            flush=True,
        )


        return {
            "text":
                best_text,

            "confidence":
                ocr_confidence,

            "votes":
                best_votes,

            "total":
                total_votes,

            "candidates":
                candidates,
        }


    # ========================================================
    # DETECT + OCR
    # ========================================================

    def detect(
        self,
        image,
    ):
        if (
            image is None
            or image.size == 0
        ):
            return []


        results = (
            self.detector.predict(
                source=image,
                verbose=False,
                conf=(
                    ANPR_CONFIDENCE
                ),
                imgsz=640,
                device="cpu",
            )
        )


        plates = []


        height, width = (
            image.shape[:2]
        )


        for result in results:

            if result.boxes is None:
                continue


            for box in result.boxes:

                confidence = float(
                    box.conf[0]
                )


                if (
                    confidence
                    < ANPR_CONFIDENCE
                ):
                    continue


                x1, y1, x2, y2 = (
                    box.xyxy[0]
                    .tolist()
                )


                x1 = max(
                    0,
                    min(
                        int(x1),
                        width - 1,
                    ),
                )

                y1 = max(
                    0,
                    min(
                        int(y1),
                        height - 1,
                    ),
                )

                x2 = max(
                    0,
                    min(
                        int(x2),
                        width,
                    ),
                )

                y2 = max(
                    0,
                    min(
                        int(y2),
                        height,
                    ),
                )


                if (
                    x2 <= x1
                    or y2 <= y1
                ):
                    continue


                crop = image[
                    y1:y2,
                    x1:x2
                ]


                if (
                    crop.size == 0
                ):
                    continue


                crop = (
                    crop.copy()
                )


                # --------------------------------------------
                # Multi-pass OCR внутри одного кадра.
                # --------------------------------------------

                try:
                    ocr_result = (
                        self
                        ._recognize_plate_multi(
                            crop
                        )
                    )

                except Exception as exc:
                    print(
                        "[ANPR] multi-pass "
                        f"OCR error: {exc}",
                        flush=True,
                    )

                    ocr_result = {
                        "text":
                            None,

                        "confidence":
                            0.0,

                        "votes":
                            0,

                        "total":
                            0,

                        "candidates":
                            [],
                    }


                text = (
                    ocr_result.get(
                        "text"
                    )
                )


                ocr_confidence = float(
                    ocr_result.get(
                        "confidence",
                        0.0,
                    )
                )


                plates.append({

                    #
                    # Confidence YOLO
                    # plate detector.
                    #
                    "confidence":
                        confidence,

                    "box": [
                        x1,
                        y1,
                        x2,
                        y2,
                    ],

                    "crop":
                        crop,

                    #
                    # Победитель OCR voting.
                    #
                    "text":
                        text or None,

                    #
                    # Agreement OCR variants.
                    #
                    "ocr_confidence":
                        ocr_confidence,

                    "ocr_votes":
                        int(
                            ocr_result.get(
                                "votes",
                                0,
                            )
                        ),

                    "ocr_total":
                        int(
                            ocr_result.get(
                                "total",
                                0,
                            )
                        ),
                })


        return plates
