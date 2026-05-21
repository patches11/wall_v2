"""
gen_cert.py — generate a self-signed TLS cert for local HTTPS.

Run once:  python gen_cert.py
Then run:  python -m uvicorn main:app --host 0.0.0.0 --port 8000 --ssl-certfile cert.pem --ssl-keyfile key.pem
Connect:   https://<your-windows-ip>:8000

The cert covers your current LAN IP + localhost so it works from both
the host machine and the phone.  It is valid for 825 days (Apple limit).

You will need to install cert.pem as a trusted CA on your phone:
  Android — Settings > Security > More security settings >
            Install from storage > CA Certificate
  iOS     — AirDrop cert.pem to yourself, install, then:
            Settings > General > About > Certificate Trust Settings > enable
"""

import datetime, ipaddress, socket
from cryptography import x509
from cryptography.x509.oid import NameOID
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa

def local_ip():
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]

ip = local_ip()
print(f"Detected local IP: {ip}")

key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

cert = (
    x509.CertificateBuilder()
    .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "wall-v2-local")]))
    .issuer_name( x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "wall-v2-local")]))
    .public_key(key.public_key())
    .serial_number(x509.random_serial_number())
    .not_valid_before(datetime.datetime.utcnow())
    .not_valid_after(datetime.datetime.utcnow() + datetime.timedelta(days=825))
    .add_extension(
        x509.SubjectAlternativeName([
            x509.IPAddress(ipaddress.IPv4Address(ip)),
            x509.IPAddress(ipaddress.IPv4Address("127.0.0.1")),
            x509.DNSName("localhost"),
        ]),
        critical=False,
    )
    .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
    .sign(key, hashes.SHA256())
)

with open("cert.pem", "wb") as f:
    f.write(cert.public_bytes(serialization.Encoding.PEM))
with open("key.pem", "wb") as f:
    f.write(key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption(),
    ))

print("Generated cert.pem and key.pem")
print(f"\nStart the server with:")
print(f"  python -m uvicorn main:app --host 0.0.0.0 --port 8000 --ssl-certfile cert.pem --ssl-keyfile key.pem")
print(f"\nOpen on phone: https://{ip}:8000")
print("\nInstall cert.pem as a trusted CA on your phone to avoid the security warning.")
