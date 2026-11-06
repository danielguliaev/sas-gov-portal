import pytest
from fastapi.testclient import TestClient
import app.core as core
from app.main import app


@pytest.fixture
def client(tmp_path):
    old = core.DB_PATH
    core.DB_PATH = str(tmp_path / "test.db")
    with TestClient(app) as c:
        yield c
    core.DB_PATH = old


@pytest.fixture
def headers():
    return {role: {"X-Demo-User": role} for role in
            ("user", "lawyer", "superadmin", "editor", "publisher", "legislator")}


@pytest.fixture
def admin(client, headers):
    def create(number, title="Тестовая норма", cats=None, crime=None, sanctions=None,
               parent="demo-intro", text="<p>Не является действующим законом.</p>"):
        body = {"code_id": "PEN", "parent_id": parent,
                "number": number, "title": title, "text_rich": text,
                "categories": cats or ["definition"], "criminal_class": crime}
        if sanctions is not None:
            body["sanctions"] = sanctions
        response = client.post("/api/v1/articles", json=body, headers=headers["superadmin"])
        assert response.status_code == 200, response.text
        return response.json()
    return create
