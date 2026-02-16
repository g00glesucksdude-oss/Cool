import cv2
import base64
import hashlib
from pyzbar.pyzbar import decode

chunks = {}
expected_total = None
expected_sha256 = None

print("📸 Point your camera at each QR code. Press 'q' to quit when done.")

cap = cv2.VideoCapture(0)
while True:
    ret, frame = cap.read()
    if not ret:
        break

    decoded_objs = decode(frame)
    for obj in decoded_objs:
        payload = obj.data.decode('utf-8')
        try:
            index_part, rest = payload.split(':', 1)
            index, total = map(int, index_part.split('/'))
            sha256, chunk = rest.split(':', 1)
        except ValueError:
            print("❌ Invalid QR format")
            continue

        if index not in chunks:
            chunks[index] = chunk
            expected_total = total
            expected_sha256 = sha256
            print(f"✅ Scanned chunk {index}/{total}")

    cv2.imshow("QR Scanner", frame)
    if cv2.waitKey(1) & 0xFF == ord('q'):
        break

cap.release()
cv2.destroyAllWindows()

# Validate and reconstruct
if len(chunks) != expected_total:
    print(f"❌ Incomplete set: {len(chunks)} of {expected_total} scanned.")
else:
    full_b64 = ''.join(chunks[i] for i in range(1, expected_total + 1))
    raw_data = base64.b64decode(full_b64)
    actual_sha256 = hashlib.sha256(raw_data).hexdigest()

    if actual_sha256 != expected_sha256:
        print("❌ Checksum mismatch! File may be corrupted.")
    else:
        with open("reconstructed.zip", "wb") as f:
            f.write(raw_data)
        print("✅ ZIP file reconstructed as 'reconstructed.zip'")
