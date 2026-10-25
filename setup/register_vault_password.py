import sys, os, getpass
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "engine"))
import vault_secret

if vault_secret.secret_exists():
    print("A secret is already registered. Delete", vault_secret.SECRET_PATH, "first if you want to replace it.")
    sys.exit(0)

# SETUP ORDER MATTERS: run this AFTER the first PIN has been set in the
# LockerDrive app (or be ready to unlock once with your recovery code).
# If the secret is registered on a vault that has a PIN (or face) set up
# but is then treated as "fresh", the app demands face re-verification
# before allowing a new PIN -- with no face enrolled yet, that path is a
# dead end and the recovery code is the only way in.
print()
print("NOTE: set up your PIN in the LockerDrive app FIRST, then register")
print("the secret here (or keep your recovery code at hand). Registering")
print("the secret before the first PIN can lock you into a face")
print("re-verification requirement with no face enrolled yet.")
print()

pw = getpass.getpass("Enter the LockerDrive vault password (not shown, not sent anywhere): ")
vault_secret.save_secret(pw)
pw = None  # drop the reference promptly
print("Saved. DPAPI-wrapped at:", vault_secret.SECRET_PATH)
print("Verifying round-trip without printing the value...")
loaded = vault_secret.load_secret()
print("Round-trip OK:", loaded is not None and len(loaded) > 0)
