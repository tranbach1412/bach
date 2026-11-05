import sqlite3
from werkzeug.security import generate_password_hash

conn = sqlite3.connect("codehub.db")
new_pass_hash = generate_password_hash("Admin@12345")

conn.execute(
    "UPDATE users SET password_hash = ?, token_version = token_version + 1 WHERE username = 'admin'", 
    (new_pass_hash,)
)
conn.commit()
conn.close()
print("Da dat lai mat khau admin thanh cong: Admin@12345")