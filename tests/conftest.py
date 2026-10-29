import pytest
from fastapi.testclient import TestClient
from jcf_platform.main import create_app, _principal
from jcf_platform import models as m

@pytest.fixture
def app():
    a=create_app('sqlite:///:memory:')
    m.Base.metadata.create_all(a.state.engine)
    a.state.claims={'sub':'maker','_scopes':{'registry.read','registry.write','registry.approve','registry.apply',
                    'reporting.read','facilities.read','contacts.read','hr.postings.read','hr.leave.read',
                    'integration.events.read','reporting.write','reporting.approve','reporting.apply',
                    'facilities.write','facilities.approve','facilities.apply','contacts.write','contacts.approve','contacts.apply',
                    'hr.write','hr.approve','hr.apply'}}
    a.dependency_overrides[_principal]=lambda:a.state.claims
    return a
@pytest.fixture
def client(app):
    with TestClient(app) as c:yield c
@pytest.fixture
def service_client(client,app):
    def mutate(action,payload,reference='ORDER-2026-01'):
        app.state.claims['sub']='maker'
        r=client.post('/api/v1/governance/requests',json={'action':action,'payload':payload,'reference':reference})
        assert r.status_code==201, r.text
        rid=r.json()['changeRequestId']
        app.state.claims['sub']='approver'
        r=client.post(f'/api/v1/governance/requests/{rid}/decision',json={'decision':'APPROVED'})
        assert r.status_code==200,r.text
        r=client.post(f'/api/v1/governance/requests/{rid}/apply')
        return r
    return mutate
