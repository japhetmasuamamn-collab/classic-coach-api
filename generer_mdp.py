from passlib.context import CryptContext

# Configuration du hachage (même config que dans ton main.py)
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# On définit les mots de passe choisis
mdp_kolwezi = "Kolwezi2026"
mdp_lubumbashi = "Lushi2026"

print(f"Hash pour Kolwezi : {pwd_context.hash(mdp_kolwezi)}")
print(f"Hash pour Lubumbashi : {pwd_context.hash(mdp_lubumbashi)}")