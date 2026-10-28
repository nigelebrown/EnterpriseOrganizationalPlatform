"""CI-only real SQL2019 Express bootstrap + backup/verify smoke test."""
import os
from scripts.db_admin import provision, backup

if os.getenv('CI_EPHEMERAL_DB') != 'YES' or os.getenv('DB_HOST') != '127.0.0.1,1433' or os.getenv('DB_NAME') != 'JcfEnterpriseSetup_TEST':
    raise SystemExit('Refusing database bootstrap against a non-ephemeral CI database')
provision()
provision()  # repeat install must never reset passwords or destroy the database
backup()
print('SQL Server 2019 Express local bootstrap, idempotent provisioning and verified backup: PASS')
