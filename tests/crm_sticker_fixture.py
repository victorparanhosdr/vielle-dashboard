"""Synthetic WebP fixtures, including animation; no provider or patient data."""
import base64
import time

STATIC = base64.b64decode("UklGRloBAABXRUJQVlA4TE0BAAAvf8AfEBcgEEgiX30NgUCSzf6ECwSSPPfneP4D/hVIbq29biNCS5Wifi6BsARWoSaYZGb1JCB+bVomv3/eM6uI/k9A+m/wkxaPpVPL+4qM29zp2Ocuh+Zk3WZOzz7zyXPMXB7NyLx9P6drf/bJdfw8Lpd+HrJvv3Efw3IYI6jSgwotKEMQMEIqoJACLSZDjwFGSAUUUqDFZOgxwAipgEIKtJgMPQYYIRVQSIEW1i31WYZhKeMJIEvudxWah3ZXoFsq3GUYlgL9BleG9lABWQrwUKB5KjAkZegeAV0SMEwZmlQBmSogFWguAUMZui1DFzBsBVoFZKvACzSfuO0B+WYElBsF1IcWoYceUoARUgGFiJcWlGsP+qgRtP7ncNnST+OT6/h5nK79WXJt39FlSpOfPMfM6dlnkmebuixp+nTsc8mxLZxre1r9tHKkf+YA")
ANIMATED = base64.b64decode("UklGRnACAABXRUJQVlA4WAoAAAASAAAAfwAAfwAAQU5JTQYAAAAAAAAAAABBTk1GkAEAAAgAAAgAAGAAAGAAAF4BAAJWUDhMeAEAAC9gABgQF6AgbQMW1viuQCCJZn+dbQQCSZz77Tn/AVT/ADkAG0fK3AEl0A8lRB3hn/u5EkjT5dmWrCG9I/rvwJGkNuqLuMFeMm8YY4xzAAAAAABOxxgjP2dA9z4hgP694V/35xrOm9JWsqatpLpze+NqLFGMKRbInGbGzGlm/AnyXuatfoJoPrsB/x0efL585s5lP+9d3uZrwTFzFhwzDyJxDH05hi47hoo5htrPMrXFLVOxTMUyFcfQOHAM4RjCMYRheKwZxiUMUyIXOcMwJUCOIowYzyXAe8oZpuEQhjKCGqAyAgqwNqsmA4wqiWZtviIQ0azN5Qyde64rFvdjo9qPhDDcFfy0auRMFk8tRiIGTDES0f9p9KIgRsx7GL1aiH7OR2lEt6Vpqb0Rpll4xLAM461IxXRQMo1Htl63uIlJ8f7aIYby/r5DLYK7hUX6f+Q37Vu/ajdd2LF/YvP3zxELJ4+Fs8rC6cY/Dy2coBbOXGunNP9cNwBBTk1GrAAAAA4AAB8AABkAABkAAF4BAABWUDhMkwAAAC8ZQAYQF6AgbQMW1viuQCCJZn+dbQQCSZz77Tn/AVT/4KiRJEfK9ea1e4bEojgHwBafgTLPUbPc6z4OEf1X2LZtk72T9gwuAPCsGIFvxwbonDe4SMXgqXM2+ErJap4SVnu4KjNI1d7oiS6FUR0W7qbgLrn2w0mzJ35v80S1vzQ8MT74HODi4Im/byC6VaQq0iOKBQA=")


def payload(key="sticker-example", *, animated=False, phone="5500000000002"):
    return {"key": {"id": key, "remoteJid": phone+"@s.whatsapp.net", "fromMe": False},
        "message": {"stickerMessage": {"mimetype": "image/webp", "isAnimated": animated,
            "fileLength": len(ANIMATED if animated else STATIC), "url": "https://provider.invalid/private",
            "mediaKey": "synthetic-private-media-key"}}, "messageTimestamp": time.time()}
