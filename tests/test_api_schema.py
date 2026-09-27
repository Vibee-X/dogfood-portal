"""OpenAPI contract coverage for the implemented DRF surface."""
from drf_spectacular.validation import validate_schema
from rest_framework.test import APIClient


def test_openapi_schema_is_valid_json_and_lists_major_api_paths():
    response = APIClient().get("/api/schema/?format=json")

    assert response.status_code == 200
    assert response["Content-Type"].startswith("application/vnd.oai.openapi+json")
    schema = response.json()
    assert schema["openapi"].startswith("3.")
    assert validate_schema(schema) is None
    assert schema["info"]["title"] == "Dogfood Portal API"
    assert "tokenAuth" in schema["components"]["securitySchemes"]

    expected_paths = {
        "/projects/new",
        "/api/judge/scores",
        "/api/judging/rubrics",
        "/api/judging/rubrics/{rubric_id}/criteria",
        "/api/judging/judges/invite",
        "/api/judging/assignments/generate",
        "/api/judging/progress",
        "/api/judging/normalization/run",
        "/api/export.csv",
        "/api/voting/ballot",
        "/api/voting/votes",
        "/api/voting/results",
        "/api/projects/{submission_id}/comments",
        "/api/comments/{comment_id}",
        "/api/comments/{comment_id}/moderation",
    }
    assert expected_paths <= set(schema["paths"])
    assert "peer judge" in schema["paths"]["/api/judge/scores"]["get"]["description"]
    assert "Organizer/admin" in schema["paths"]["/api/voting/results"]["get"]["description"]
