import json


def _get(config, key, default=None):
    if isinstance(config, dict):
        return config.get(key, default)

    return getattr(config, key, default)


def _parse_object(raw, field_name):
    raw = (raw or "").strip()

    if not raw:
        return {}

    try:
        value = json.loads(raw)
    except Exception as exc:
        raise ValueError(
            f"{field_name}: некорректный JSON: {exc}"
        )

    if not isinstance(value, dict):
        raise ValueError(
            f"{field_name}: JSON должен быть объектом"
        )

    return value


def build_request(config, test=False):
    """
    Единая сборка HTTP-запроса для:
      - реального webhook из CameraWorker
      - кнопки "Тест"

    Новая схема:
      headers_json
      body_json

    Для старых webhook без этих полей сохраняется
    старое поведение через token/device_key/duration_ms.
    """

    method = (
        str(_get(config, "method", "POST") or "POST")
        .strip()
        .upper()
    )

    url = str(
        _get(config, "url", "") or ""
    ).strip()

    timeout_value = max(
        1.0,
        float(_get(config, "timeout", 20) or 20),
    )

    headers_raw = (
        _get(config, "headers_json", "{}")
        or ""
    ).strip()

    body_raw = (
        _get(config, "body_json", "{}")
        or ""
    ).strip()

    # Новая схема считается активной,
    # если headers_json или body_json заполнены.
    if headers_raw or body_raw:
        headers_obj = _parse_object(
            headers_raw,
            "Заголовки",
        )

        body_obj = _parse_object(
            body_raw,
            "Тело запроса",
        )

        headers = {
            str(k): str(v)
            for k, v in headers_obj.items()
        }

        body = body_obj

    else:
        # Совместимость со старой схемой.
        content_type = (
            _get(config, "content_type")
            or "application/json"
        )

        headers = {
            "Content-Type": content_type
        }

        body = {}

        token = _get(config, "token")
        device_key = _get(config, "device_key")
        duration_ms = _get(config, "duration_ms")

        if token:
            body["token"] = token

        if device_key:
            body["device_key"] = device_key

        if (
            duration_ms is not None
            and int(duration_ms) >= 0
        ):
            body["duration_ms"] = int(duration_ms)

        extra = (
            _get(config, "extra_payload")
            or ""
        ).strip()

        if extra:
            extra_obj = _parse_object(
                extra,
                "Дополнительный JSON",
            )
            body.update(extra_obj)

    if test:
        headers["X-Plate-Monitor-Test"] = "1"

    return (
        method,
        url,
        headers,
        body,
        timeout_value,
    )
