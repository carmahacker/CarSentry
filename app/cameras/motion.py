import time
import cv2
import numpy as np

class MotionDetector:
    def __init__(self, threshold=25, min_changed_ratio=0.01, idle_timeout=2.0):
        self.threshold=threshold; self.min_changed_ratio=min_changed_ratio; self.idle_timeout=idle_timeout
        self.prev=None; self.active=False; self.last_motion=0.0

    def update(self, frame):
        small=cv2.resize(frame,(640,int(frame.shape[0]*640/frame.shape[1])))
        gray=cv2.GaussianBlur(cv2.cvtColor(small,cv2.COLOR_BGR2GRAY),(21,21),0)
        if self.prev is None:
            self.prev=gray; return "idle"
        diff=cv2.absdiff(self.prev,gray)
        self.prev=gray
        _,th=cv2.threshold(diff,self.threshold,255,cv2.THRESH_BINARY)
        th=cv2.dilate(th,None,iterations=2)
        ratio=float(np.count_nonzero(th))/th.size
        moving=ratio>=self.min_changed_ratio
        now=time.monotonic()
        if moving:
            self.last_motion=now
            if not self.active:
                self.active=True; return "started"
            return "detected"
        if self.active and now-self.last_motion>=self.idle_timeout:
            self.active=False; return "finished"
        return "idle"
