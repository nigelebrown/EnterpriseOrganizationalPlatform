"""No-prompt HRIS_Duplicate bootstrap, no real credentials in tests or GitHub."""
import json
from pathlib import Path
from zipfile import ZipFile
import stat
from scripts import configure_pms_source as source


def test_metadata_from_adreset_settings_and_mode():
    config = {**source.DEFAULT, "Password": "sample-only-secret"}
    result = source.prepare(config)
    assert result["PMS_SOURCE_HOST"] == "172.17.51.49,1433"
    assert result["PMS_SOURCE_DB"] == "HRIS_Duplicate"
    assert result["PMS_SOURCE_USER"] == "HRISUser"
    assert result["PMS_SOURCE_PASSWORD"] == "sample-only-secret"
    assert result["PMS_SOURCE_TRUST_CERT"] == "false"


def test_import_adreset_zip_without_exposing_password(tmp_path):
    zip_path = tmp_path / "adreset.zip"
    with ZipFile(zip_path, "w") as z:
        z.writestr("App/Api/appsettings.json",
                   json.dumps({"Hris": {**source.DEFAULT, "Password": "test-placeholder"}}))
    extracted = source.from_zip(zip_path)
    assert extracted["Database"] == "HRIS_Duplicate"
    file_path = tmp_path / "pms-source.env"
    source.write_secret(file_path, source.prepare(extracted))
    assert file_path.stat().st_mode & 0o777 == 0o600
    text = file_path.read_text()
    assert "PMS_SOURCE_HOST=172.17.51.49,1433" in text
    assert "PMS_SOURCE_PASSWORD=test-placeholder" in text
    # Idempotent, does not append duplicate definitions.
    source.write_secret(file_path, source.prepare(extracted))
    assert file_path.read_text().count("PMS_SOURCE_HOST=") == 1


def test_reject_blank_or_multiline_password():
    for password in ("", "broken\npassword"):
        try:
            source.prepare({**source.DEFAULT, "Password": password})
        except ValueError:
            pass
        else:
            assert False, "Must reject empty/multiline password"


def test_reject_registry_target_as_source():
    try:
        source.prepare({**source.DEFAULT, "Database": "JcfEnterpriseRegistry",
                        "Password": "example"})
    except ValueError:
        pass
    else:
        assert False, "Must never connect to own destination"


def test_reuse_existing_source_password_changes_database_and_tls_only(tmp_path):
    existing = tmp_path / "pms-source.env"
    source.write_secret(existing, {
        "PMS_SOURCE_HOST": "172.17.51.49,1433",
        "PMS_SOURCE_DB": "HRIS_Copy",
        "PMS_SOURCE_USER": "HRISUser",
        "PMS_SOURCE_PASSWORD": "test-placeholder-secret",
        "PMS_SOURCE_TRUST_CERT": "false",
    })
    result = source.prepare(source.from_existing(existing), trust_cert=True)
    source.write_secret(existing, result)
    parsed = existing.read_text()
    assert "PMS_SOURCE_DB=HRIS_Duplicate" in parsed
    assert "PMS_SOURCE_TRUST_CERT=true" in parsed
    assert "PMS_SOURCE_PASSWORD=test-placeholder-secret" in parsed
    assert existing.stat().st_mode & 0o777 == 0o600
    assert parsed.count("PMS_SOURCE_PASSWORD=")==1
