"""JcfAdReset API consumer contract for JCF-EMP authority and OAuth app roles."""
from jcf_platform.main import _authorized_permissions

def test_delegated_registry_scopes():
    assert _authorized_permissions({'scp':'registry.read hr.postings.read'}) == {
        'registry.read','hr.postings.read'}

def test_approved_application_roles():
    assert _authorized_permissions({'roles':['registry.read','hr.postings.read']}) == {
        'registry.read','hr.postings.read'}

def test_unrelated_roles_do_not_grant_registry_access():
    assert 'registry.read' not in _authorized_permissions({'roles':['contacts.read']})

def test_empty_claims_have_no_permissions():
    assert _authorized_permissions({}) == set()
