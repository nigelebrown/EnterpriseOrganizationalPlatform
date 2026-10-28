"""Branding contract: presentation changes must not alter existing integration identifiers."""
from pathlib import Path
from jcf_platform.main import create_app

ROOT = Path(__file__).resolve().parents[1]
APP_NAME = "JCF Enterprise Management Platform (JCF-EMP)"
TAGLINE = "One Platform. Standardized Data. Integrated Services."

def test_admin_branding():
    markup = (ROOT / "jcf_platform" / "admin.html").read_text(encoding="utf-8")
    assert f"<title>{APP_NAME}</title>" in markup
    assert APP_NAME.replace(" (JCF-EMP)", "") in markup
    assert TAGLINE in markup
    assert "JCF Enterprise Organizational Platform" not in markup

def test_api_openapi_branding():
    app = create_app("sqlite:///:memory:")
    try:
        assert app.title == APP_NAME
        schema = app.openapi()
        assert schema["info"]["title"] == APP_NAME
        assert "/api/v1/registry/locations" in schema["paths"]
        assert "/api/v1/registry/locations/{location_id}/sections" in schema["paths"]
    finally:
        app.state.engine.dispose()

def test_identity_preservation():
    # These identifiers are deliberately stable across a branding-only release.
    compose = (ROOT / "deploy" / "compose.yaml").read_text(encoding="utf-8")
    assert "name: jcf-enterprise" in compose
    config = (ROOT / "jcf_platform" / "config.py").read_text(encoding="utf-8")
    assert "JcfEnterpriseRegistry" in config
    package = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert 'name = "jcf-enterprise-organizational-platform"' in package
