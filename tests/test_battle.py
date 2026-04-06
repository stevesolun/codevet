"""Battle tests: 30 real-world scenarios testing the full codevet pipeline.

Each test creates realistic buggy code (the kind AI tools produce), mocks
Ollama to return plausible test code, mocks Docker to return realistic
pytest output, runs the pipeline, and makes strong assertions.

Organised into 6 categories of 5 tests each.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from codevet.models import GeneratedTest, SandboxConfig, VetResult
from codevet.scorer import calculate_confidence, score_fix
from codevet.vetter import Vetter, combine_test_cases, parse_pytest_output

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_ollama_response(test_code: str) -> dict:
    """Wrap test code in the dict shape Ollama returns."""
    return {"message": {"content": test_code}}


def _make_critique_response(score: int, reasoning: str) -> dict:
    """Wrap a critique JSON response for the scorer."""
    import json

    return {"message": {"content": json.dumps({"score": score, "reasoning": reasoning})}}


def _setup_mocks(
    mock_docker: MagicMock,
    mock_ollama: MagicMock,
    test_code_response: str,
    pytest_stdout: str,
    exit_code: int = 1,
) -> None:
    """Configure Docker and Ollama mocks for a vet() call."""
    # Ollama client
    ollama_client = MagicMock()
    ollama_client.list.return_value = {}
    ollama_client.chat.return_value = _make_ollama_response(test_code_response)
    mock_ollama.Client.return_value = ollama_client

    # Docker client
    container = MagicMock()
    container.wait.return_value = {"StatusCode": exit_code}
    container.logs.return_value = pytest_stdout.encode("utf-8")
    docker_client = MagicMock()
    docker_client.containers.run.return_value = container
    docker_client.images.get.return_value = True
    mock_docker.from_env.return_value = docker_client


def _run_vet(mock_docker: MagicMock, mock_ollama: MagicMock, buggy_code: str, file_name: str = "code.py") -> VetResult:
    """Run the Vetter.vet() pipeline with pre-configured mocks."""
    config = SandboxConfig(project_dir=Path.cwd())
    vetter = Vetter(model="test-model")

    from codevet.sandbox import Sandbox

    with Sandbox(config) as sb:
        return vetter.vet(buggy_code, file_name, sb)


# ===================================================================
# Category 1: SQL Injection Variants (tests 1-5)
# ===================================================================


class TestBattleSQLInjection:
    """SQL injection bugs that AI tools commonly produce."""

    @patch("codevet.sandbox.docker")
    @patch("codevet.vetter.ollama")
    def test_battle_01_fstring_query_flask(self, mock_ollama: MagicMock, mock_docker: MagicMock) -> None:
        """f-string query in a Flask route handler."""
        buggy_code = '''\
from flask import Flask, request
import sqlite3

app = Flask(__name__)

@app.route("/user")
def get_user():
    username = request.args.get("username")
    conn = sqlite3.connect("app.db")
    cursor = conn.cursor()
    query = f"SELECT * FROM users WHERE username = '{username}'"
    cursor.execute(query)
    row = cursor.fetchone()
    conn.close()
    if row:
        return {"id": row[0], "name": row[1]}
    return {"error": "not found"}, 404
'''

        test_code = '''\
import pytest

def test_security_sql_injection_fstring():
    """Verify f-string SQL is vulnerable to injection."""
    payload = "' OR '1'='1"
    query = f"SELECT * FROM users WHERE username = '{payload}'"
    assert "OR" in query
    assert "1'='1" in query

def test_unit_get_user_returns_dict():
    """Basic happy path returns a dict."""
    assert True

def test_edge_empty_username():
    """Empty username should not crash."""
    assert True
'''

        _setup_mocks(mock_docker, mock_ollama, test_code, "1 passed, 2 failed in 0.3s", exit_code=1)
        vet_result = _run_vet(mock_docker, mock_ollama, buggy_code, "app.py")

        assert isinstance(vet_result, VetResult)
        assert vet_result.failed == 2
        assert vet_result.passed == 1
        assert any(tc.category == "security" for tc in vet_result.test_cases)

        confidence = score_fix(vet_result, '{"score": 25, "reasoning": "SQL injection via f-string"}')
        assert confidence.score <= 50, "Vulnerable code should score low"
        assert confidence.grade in ("D", "F")

    @patch("codevet.sandbox.docker")
    @patch("codevet.vetter.ollama")
    def test_battle_02_format_query_django_raw(self, mock_ollama: MagicMock, mock_docker: MagicMock) -> None:
        """.format() query in Django ORM raw SQL."""
        buggy_code = '''\
from django.db import connection

def search_products(category, min_price):
    with connection.cursor() as cursor:
        query = "SELECT * FROM products WHERE category = '{}' AND price > {}".format(
            category, min_price
        )
        cursor.execute(query)
        return cursor.fetchall()
'''

        test_code = '''\
import pytest

def test_security_format_injection():
    """Detect .format() SQL injection."""
    category = "'; DROP TABLE products; --"
    query = "SELECT * FROM products WHERE category = '{}' AND price > {}".format(category, 10)
    assert "DROP TABLE" in query

def test_unit_search_products_basic():
    """Search products returns results."""
    assert True

def test_edge_negative_price():
    """Negative price input should be handled."""
    assert True
'''

        _setup_mocks(mock_docker, mock_ollama, test_code, "1 passed, 2 failed in 0.2s", exit_code=1)
        vet_result = _run_vet(mock_docker, mock_ollama, buggy_code, "products.py")

        assert vet_result.failed == 2
        assert vet_result.passed == 1
        assert len(vet_result.test_cases) == 3

        confidence = score_fix(vet_result, '{"score": 20, "reasoning": ".format() SQL injection"}')
        assert confidence.score < 45

    @patch("codevet.sandbox.docker")
    @patch("codevet.vetter.ollama")
    def test_battle_03_concat_sqlalchemy_text(self, mock_ollama: MagicMock, mock_docker: MagicMock) -> None:
        """String concatenation in SQLAlchemy text()."""
        buggy_code = '''\
from sqlalchemy import text, create_engine

engine = create_engine("sqlite:///app.db")

def find_orders(customer_id):
    query = text("SELECT * FROM orders WHERE customer_id = " + str(customer_id))
    with engine.connect() as conn:
        return conn.execute(query).fetchall()
'''

        test_code = '''\
import pytest

def test_security_concat_injection():
    """String concat in text() allows injection."""
    customer_id = "1 OR 1=1"
    query_str = "SELECT * FROM orders WHERE customer_id = " + str(customer_id)
    assert "OR 1=1" in query_str

def test_unit_find_orders_type():
    """find_orders returns a list."""
    assert True

def test_edge_zero_customer_id():
    """Zero customer_id edge case."""
    assert True
'''

        _setup_mocks(mock_docker, mock_ollama, test_code, "1 passed, 2 failed in 0.2s", exit_code=1)
        vet_result = _run_vet(mock_docker, mock_ollama, buggy_code, "orders.py")

        assert vet_result.failed == 2
        assert vet_result.passed == 1
        assert any(tc.category == "security" for tc in vet_result.test_cases)

    @patch("codevet.sandbox.docker")
    @patch("codevet.vetter.ollama")
    def test_battle_04_multiline_query_builder(self, mock_ollama: MagicMock, mock_docker: MagicMock) -> None:
        """Multi-line query builder with user input interpolation."""
        buggy_code = '''\
import sqlite3

def advanced_search(db_path, filters):
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    query = "SELECT * FROM items WHERE 1=1"
    if filters.get("name"):
        query += f" AND name LIKE '%{filters['name']}%'"
    if filters.get("status"):
        query += f" AND status = '{filters['status']}'"
    if filters.get("min_price"):
        query += f" AND price >= {filters['min_price']}"
    cursor.execute(query)
    results = cursor.fetchall()
    conn.close()
    return results
'''

        test_code = '''\
import pytest

def test_security_multiline_name_injection():
    """Name filter allows injection."""
    filters = {"name": "'; DROP TABLE items; --"}
    query = "SELECT * FROM items WHERE 1=1"
    query += f" AND name LIKE '%{filters['name']}%'"
    assert "DROP TABLE" in query

def test_security_multiline_status_injection():
    """Status filter allows injection."""
    filters = {"status": "' OR '1'='1"}
    query = "SELECT * FROM items WHERE 1=1"
    query += f" AND status = '{filters['status']}'"
    assert "OR" in query

def test_unit_advanced_search_no_filters():
    """No filters returns base query."""
    assert True

def test_edge_empty_filters():
    """Empty dict produces valid base query."""
    assert True
'''

        _setup_mocks(mock_docker, mock_ollama, test_code, "2 passed, 2 failed in 0.3s", exit_code=1)
        vet_result = _run_vet(mock_docker, mock_ollama, buggy_code, "search.py")

        assert vet_result.failed == 2
        assert vet_result.passed == 2
        security_tests = [tc for tc in vet_result.test_cases if tc.category == "security"]
        assert len(security_tests) >= 2

    @patch("codevet.sandbox.docker")
    @patch("codevet.vetter.ollama")
    def test_battle_05_fstring_table_name(self, mock_ollama: MagicMock, mock_docker: MagicMock) -> None:
        """Parameterized query that accidentally uses f-string for table name."""
        buggy_code = '''\
import sqlite3

def get_records(db_path, table_name, record_id):
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    # Parameterized value but f-string table name!
    query = f"SELECT * FROM {table_name} WHERE id = ?"
    cursor.execute(query, (record_id,))
    row = cursor.fetchone()
    conn.close()
    return row
'''

        test_code = '''\
import pytest

def test_security_table_name_injection():
    """Table name via f-string is injectable."""
    table_name = "users; DROP TABLE users; --"
    query = f"SELECT * FROM {table_name} WHERE id = ?"
    assert "DROP TABLE" in query

def test_unit_get_records_parameterized_value():
    """Value parameter uses ? placeholder correctly."""
    query = f"SELECT * FROM safe_table WHERE id = ?"
    assert "?" in query

def test_edge_none_table_name():
    """None table_name should raise."""
    assert True
'''

        _setup_mocks(mock_docker, mock_ollama, test_code, "1 passed, 2 failed in 0.2s", exit_code=1)
        vet_result = _run_vet(mock_docker, mock_ollama, buggy_code, "records.py")

        assert vet_result.failed == 2
        assert vet_result.passed == 1

        confidence = score_fix(vet_result, '{"score": 30, "reasoning": "Table name still injectable"}')
        assert confidence.score < 50


# ===================================================================
# Category 2: Authentication & Authorization Bugs (tests 6-10)
# ===================================================================


class TestBattleAuthBugs:
    """Authentication and authorization bugs from AI-generated code."""

    @patch("codevet.sandbox.docker")
    @patch("codevet.vetter.ollama")
    def test_battle_06_jwt_no_expiry_check(self, mock_ollama: MagicMock, mock_docker: MagicMock) -> None:
        """JWT token validation that doesn't check expiry."""
        buggy_code = '''\
import jwt

SECRET = "mysecret"

def validate_token(token):
    try:
        payload = jwt.decode(token, SECRET, algorithms=["HS256"], options={"verify_exp": False})
        return payload
    except jwt.InvalidTokenError:
        return None
'''

        test_code = '''\
import pytest

def test_security_jwt_expired_token_accepted():
    """Expired tokens should be rejected but verify_exp is False."""
    import jwt, time
    payload = {"user": "admin", "exp": int(time.time()) - 3600}
    token = jwt.encode(payload, "mysecret", algorithm="HS256")
    # With verify_exp=False, expired tokens are accepted - this is a bug
    result = jwt.decode(token, "mysecret", algorithms=["HS256"], options={"verify_exp": False})
    assert result is not None  # Bug: should reject expired tokens

def test_unit_valid_token():
    """Valid token returns payload."""
    assert True

def test_edge_empty_token():
    """Empty token string should return None."""
    assert True
'''

        _setup_mocks(mock_docker, mock_ollama, test_code, "2 passed, 1 failed in 0.3s", exit_code=1)
        vet_result = _run_vet(mock_docker, mock_ollama, buggy_code, "auth.py")

        assert vet_result.failed == 1
        assert vet_result.passed == 2
        assert any(tc.category == "security" for tc in vet_result.test_cases)

        confidence = score_fix(vet_result, '{"score": 35, "reasoning": "JWT expiry not validated"}')
        assert confidence.score < 65

    @patch("codevet.sandbox.docker")
    @patch("codevet.vetter.ollama")
    def test_battle_07_password_eq_comparison(self, mock_ollama: MagicMock, mock_docker: MagicMock) -> None:
        """Password comparison using == instead of constant-time compare."""
        buggy_code = '''\
import hashlib

def verify_password(stored_hash, password, salt):
    computed = hashlib.sha256((salt + password).encode()).hexdigest()
    return computed == stored_hash  # Timing side-channel!
'''

        test_code = '''\
import pytest

def test_security_timing_attack_password():
    """== comparison leaks timing info vs hmac.compare_digest."""
    import hashlib, hmac
    h1 = hashlib.sha256(b"salt1pass1").hexdigest()
    h2 = hashlib.sha256(b"salt1pass2").hexdigest()
    # Using == is a security issue (timing side-channel)
    assert (h1 == h2) == False

def test_unit_correct_password_matches():
    """Correct password returns True."""
    assert True

def test_edge_empty_password():
    """Empty password should still compute hash."""
    assert True
'''

        _setup_mocks(mock_docker, mock_ollama, test_code, "2 passed, 1 failed in 0.2s", exit_code=1)
        vet_result = _run_vet(mock_docker, mock_ollama, buggy_code, "auth.py")

        assert vet_result.failed == 1
        assert vet_result.passed == 2

    @patch("codevet.sandbox.docker")
    @patch("codevet.vetter.ollama")
    def test_battle_08_empty_role_admin_access(self, mock_ollama: MagicMock, mock_docker: MagicMock) -> None:
        """Role check that allows admin access with empty role string."""
        buggy_code = '''\
def check_admin(user):
    role = user.get("role", "")
    if role != "user":  # Bug: empty string also != "user"
        return True  # Grants admin access!
    return False
'''

        test_code = '''\
import pytest

def test_security_empty_role_grants_admin():
    """Empty role string bypasses the check."""
    user = {"name": "attacker", "role": ""}
    role = user.get("role", "")
    result = role != "user"
    assert result == True  # Bug! Empty string gets admin

def test_security_none_role_grants_admin():
    """Missing role key grants admin."""
    user = {"name": "attacker"}
    role = user.get("role", "")
    result = role != "user"
    assert result == True  # Bug!

def test_unit_admin_role_returns_true():
    """Admin role should return True."""
    assert True

def test_unit_user_role_returns_false():
    """User role should return False."""
    assert True
'''

        _setup_mocks(mock_docker, mock_ollama, test_code, "2 passed, 2 failed in 0.2s", exit_code=1)
        vet_result = _run_vet(mock_docker, mock_ollama, buggy_code, "rbac.py")

        assert vet_result.failed == 2
        assert vet_result.passed == 2
        security_tests = [tc for tc in vet_result.test_cases if tc.category == "security"]
        assert len(security_tests) >= 2

    @patch("codevet.sandbox.docker")
    @patch("codevet.vetter.ollama")
    def test_battle_09_localstorage_session_token(self, mock_ollama: MagicMock, mock_docker: MagicMock) -> None:
        """Session token stored in localStorage (XSS-vulnerable pattern)."""
        buggy_code = '''\
def create_login_response(user_id, token):
    """Return JS snippet that stores token in localStorage."""
    return f"""
    <script>
        localStorage.setItem('auth_token', '{token}');
        window.location.href = '/dashboard';
    </script>
    """
'''

        test_code = '''\
import pytest

def test_security_xss_localstorage_token():
    """Token in localStorage is accessible via XSS."""
    token = "abc123"
    html = f"localStorage.setItem('auth_token', '{token}')"
    assert "localStorage" in html  # XSS-vulnerable pattern

def test_security_script_injection_in_token():
    """Malicious token value can break out of script."""
    token = "'); alert('xss'); //"
    html = f"localStorage.setItem('auth_token', '{token}')"
    assert "alert" in html

def test_unit_login_response_contains_redirect():
    """Response includes redirect."""
    assert True
'''

        _setup_mocks(mock_docker, mock_ollama, test_code, "1 passed, 2 failed in 0.2s", exit_code=1)
        vet_result = _run_vet(mock_docker, mock_ollama, buggy_code, "login.py")

        assert vet_result.failed == 2
        assert vet_result.passed == 1
        assert any(tc.category == "security" for tc in vet_result.test_cases)

    @patch("codevet.sandbox.docker")
    @patch("codevet.vetter.ollama")
    def test_battle_10_api_key_timing_sidechannel(self, mock_ollama: MagicMock, mock_docker: MagicMock) -> None:
        """API key validation with timing side-channel."""
        buggy_code = '''\
VALID_API_KEYS = {"sk-prod-abc123", "sk-prod-def456"}

def validate_api_key(key):
    for valid_key in VALID_API_KEYS:
        if key == valid_key:  # Timing side-channel via ==
            return True
    return False
'''

        test_code = '''\
import pytest

def test_security_timing_sidechannel_api_key():
    """== comparison on API keys leaks timing info."""
    # This test documents the vulnerability
    assert "sk-prod-abc123" == "sk-prod-abc123"  # Uses ==, not constant-time

def test_unit_valid_key_accepted():
    """Valid API key returns True."""
    assert True

def test_edge_empty_key():
    """Empty string key returns False."""
    assert True
'''

        _setup_mocks(mock_docker, mock_ollama, test_code, "2 passed, 1 failed in 0.2s", exit_code=1)
        vet_result = _run_vet(mock_docker, mock_ollama, buggy_code, "api_auth.py")

        assert vet_result.failed == 1
        assert vet_result.passed == 2

        confidence = score_fix(vet_result, '{"score": 40, "reasoning": "Timing side-channel in API key check"}')
        assert confidence.score < 65


# ===================================================================
# Category 3: Data Processing Edge Cases (tests 11-15)
# ===================================================================


class TestBattleDataProcessing:
    """Data processing edge cases AI tools commonly miss."""

    @patch("codevet.sandbox.docker")
    @patch("codevet.vetter.ollama")
    def test_battle_11_pandas_empty_dataframe(self, mock_ollama: MagicMock, mock_docker: MagicMock) -> None:
        """Pandas DataFrame operation that crashes on empty DataFrame."""
        buggy_code = '''\
import pandas as pd

def get_top_performers(df, n=5):
    sorted_df = df.sort_values("score", ascending=False)
    top = sorted_df.head(n)
    avg_score = top["score"].mean()  # NaN on empty df
    return {
        "top_users": top["name"].tolist(),
        "average_score": avg_score,
        "highest": top["score"].iloc[0],  # IndexError on empty!
    }
'''

        test_code = '''\
import pytest

def test_edge_empty_dataframe_crashes():
    """Empty DataFrame causes IndexError on iloc[0]."""
    import pandas as pd
    df = pd.DataFrame(columns=["name", "score"])
    with pytest.raises(IndexError):
        sorted_df = df.sort_values("score", ascending=False)
        sorted_df.head(5)["score"].iloc[0]

def test_unit_top_performers_basic():
    """Normal case returns correct top N."""
    assert True

def test_edge_none_scores():
    """NaN scores should be handled."""
    assert True
'''

        _setup_mocks(mock_docker, mock_ollama, test_code, "1 passed, 2 failed in 0.3s", exit_code=1)
        vet_result = _run_vet(mock_docker, mock_ollama, buggy_code, "analytics.py")

        assert vet_result.failed == 2
        assert vet_result.passed == 1
        edge_tests = [tc for tc in vet_result.test_cases if tc.category == "edge"]
        assert len(edge_tests) >= 1

    @patch("codevet.sandbox.docker")
    @patch("codevet.vetter.ollama")
    def test_battle_12_json_nested_null(self, mock_ollama: MagicMock, mock_docker: MagicMock) -> None:
        """JSON parser that doesn't handle nested null values."""
        buggy_code = '''\
def extract_user_email(data):
    return data["user"]["profile"]["email"].lower()  # Crashes if any is None
'''

        test_code = '''\
import pytest

def test_edge_none_profile():
    """None profile causes AttributeError."""
    data = {"user": {"profile": None}}
    with pytest.raises((TypeError, AttributeError)):
        data["user"]["profile"]["email"]

def test_edge_none_email():
    """None email causes AttributeError on .lower()."""
    data = {"user": {"profile": {"email": None}}}
    with pytest.raises(AttributeError):
        data["user"]["profile"]["email"].lower()

def test_unit_valid_nested_email():
    """Valid nested data returns email."""
    assert True
'''

        _setup_mocks(mock_docker, mock_ollama, test_code, "1 passed, 2 failed in 0.2s", exit_code=1)
        vet_result = _run_vet(mock_docker, mock_ollama, buggy_code, "parser.py")

        assert vet_result.failed == 2
        assert vet_result.passed == 1
        assert any(tc.category == "edge" for tc in vet_result.test_cases)

    @patch("codevet.sandbox.docker")
    @patch("codevet.vetter.ollama")
    def test_battle_13_csv_unicode_break(self, mock_ollama: MagicMock, mock_docker: MagicMock) -> None:
        """CSV reader that breaks on unicode characters."""
        buggy_code = '''\
def read_csv_names(filepath):
    with open(filepath, "r") as f:  # Missing encoding parameter
        lines = f.readlines()
    names = []
    for line in lines[1:]:  # Skip header
        parts = line.strip().split(",")
        names.append(parts[0])
    return names
'''

        test_code = '''\
import pytest

def test_edge_unicode_csv_read():
    """File with unicode chars may fail without encoding param."""
    # open() without encoding uses system default, may fail on non-ASCII
    assert True  # Structural test for the pattern

def test_unit_read_csv_basic():
    """Basic CSV reading works."""
    assert True

def test_edge_empty_csv_file():
    """Empty CSV should return empty list."""
    assert True
'''

        _setup_mocks(mock_docker, mock_ollama, test_code, "2 passed, 1 failed in 0.2s", exit_code=1)
        vet_result = _run_vet(mock_docker, mock_ollama, buggy_code, "csv_reader.py")

        assert vet_result.failed == 1
        assert vet_result.passed == 2

    @patch("codevet.sandbox.docker")
    @patch("codevet.vetter.ollama")
    def test_battle_14_leap_year_date_parse(self, mock_ollama: MagicMock, mock_docker: MagicMock) -> None:
        """Date parser that fails on leap year edge case."""
        buggy_code = '''\
def days_in_month(year, month):
    days = [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
    return days[month - 1]  # Bug: no leap year check for February
'''

        test_code = '''\
import pytest

def test_edge_leap_year_february():
    """Feb in leap year should have 29 days."""
    days = [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
    result = days[2 - 1]
    assert result == 28  # Bug: should be 29 for leap year

def test_unit_january_days():
    """January has 31 days."""
    assert True

def test_edge_boundary_month_zero():
    """Month 0 should raise or handle gracefully."""
    assert True
'''

        _setup_mocks(mock_docker, mock_ollama, test_code, "1 passed, 2 failed in 0.2s", exit_code=1)
        vet_result = _run_vet(mock_docker, mock_ollama, buggy_code, "dates.py")

        assert vet_result.failed == 2
        assert vet_result.passed == 1
        edge_tests = [tc for tc in vet_result.test_cases if tc.category == "edge"]
        assert len(edge_tests) >= 1

    @patch("codevet.sandbox.docker")
    @patch("codevet.vetter.ollama")
    def test_battle_15_float_eq_comparison(self, mock_ollama: MagicMock, mock_docker: MagicMock) -> None:
        """Float comparison using == instead of math.isclose()."""
        buggy_code = '''\
def calculate_discount(price, discount_pct):
    discount = price * (discount_pct / 100)
    final = price - discount
    if final == 0.0:  # Bug: float comparison with ==
        return 0.0
    return round(final, 2)

def are_prices_equal(a, b):
    return a == b  # Bug: float equality
'''

        test_code = '''\
import pytest

def test_edge_float_equality_fails():
    """Float == comparison fails for 0.1 + 0.2."""
    result = 0.1 + 0.2
    assert result != 0.3  # This is True! Floats are imprecise

def test_unit_discount_basic():
    """10% off 100 should be 90."""
    assert True

def test_edge_zero_price():
    """Zero price with discount."""
    assert True
'''

        _setup_mocks(mock_docker, mock_ollama, test_code, "1 passed, 2 failed in 0.2s", exit_code=1)
        vet_result = _run_vet(mock_docker, mock_ollama, buggy_code, "pricing.py")

        assert vet_result.failed == 2
        assert vet_result.passed == 1

        confidence = score_fix(vet_result, '{"score": 30, "reasoning": "Float comparison bug persists"}')
        assert confidence.score < 50


# ===================================================================
# Category 4: Concurrency & Resource Bugs (tests 16-20)
# ===================================================================


class TestBattleConcurrencyResource:
    """Concurrency and resource management bugs."""

    @patch("codevet.sandbox.docker")
    @patch("codevet.vetter.ollama")
    def test_battle_16_file_handle_leak(self, mock_ollama: MagicMock, mock_docker: MagicMock) -> None:
        """File handle never closed (missing context manager)."""
        buggy_code = '''\
def read_config(path):
    f = open(path, "r")  # Bug: no context manager, no close
    data = f.read()
    config = {}
    for line in data.strip().split("\\n"):
        if "=" in line:
            key, val = line.split("=", 1)
            config[key.strip()] = val.strip()
    return config  # f is never closed!
'''

        test_code = '''\
import pytest

def test_security_file_handle_leak():
    """File handle is never closed - resource leak."""
    # Pattern detection: open() without with statement
    code = "f = open(path, 'r')"
    assert "with" not in code  # Bug: no context manager

def test_unit_read_config_parses():
    """Config parsing works for valid input."""
    assert True

def test_edge_empty_config_file():
    """Empty file returns empty dict."""
    assert True
'''

        _setup_mocks(mock_docker, mock_ollama, test_code, "1 passed, 2 failed in 0.2s", exit_code=1)
        vet_result = _run_vet(mock_docker, mock_ollama, buggy_code, "config.py")

        assert vet_result.failed == 2
        assert vet_result.passed == 1

    @patch("codevet.sandbox.docker")
    @patch("codevet.vetter.ollama")
    def test_battle_17_thread_unsafe_counter(self, mock_ollama: MagicMock, mock_docker: MagicMock) -> None:
        """Thread-unsafe counter increment."""
        buggy_code = '''\
class RequestCounter:
    def __init__(self):
        self.count = 0  # Not thread-safe!

    def increment(self):
        current = self.count
        self.count = current + 1  # Race condition: read-modify-write

    def get_count(self):
        return self.count
'''

        test_code = '''\
import pytest

def test_security_thread_unsafe_counter():
    """Counter increment is not atomic - race condition."""
    # read-modify-write pattern without lock
    counter = type("Counter", (), {"count": 0})()
    counter.count = counter.count + 1  # Not atomic
    assert counter.count == 1  # Works single-threaded, fails multi-threaded

def test_unit_counter_increments():
    """Single-threaded increment works."""
    assert True

def test_edge_zero_count():
    """Initial count is zero."""
    assert True
'''

        _setup_mocks(mock_docker, mock_ollama, test_code, "2 passed, 1 failed in 0.2s", exit_code=1)
        vet_result = _run_vet(mock_docker, mock_ollama, buggy_code, "counter.py")

        assert vet_result.failed == 1
        assert vet_result.passed == 2

    @patch("codevet.sandbox.docker")
    @patch("codevet.vetter.ollama")
    def test_battle_18_connection_pool_exhaustion(self, mock_ollama: MagicMock, mock_docker: MagicMock) -> None:
        """Database connection pool exhaustion (connections not returned)."""
        buggy_code = '''\
import sqlite3

_pool = []

def get_connection(db_path):
    conn = sqlite3.connect(db_path)
    _pool.append(conn)
    return conn  # Bug: connection never returned to pool or closed

def query(db_path, sql):
    conn = get_connection(db_path)
    cursor = conn.cursor()
    cursor.execute(sql)
    return cursor.fetchall()
    # conn is never closed or returned!
'''

        test_code = '''\
import pytest

def test_security_connection_leak():
    """Connections are never closed - pool exhaustion."""
    pool = []
    for i in range(100):
        pool.append(f"connection_{i}")
    assert len(pool) == 100  # Pool grows unbounded

def test_unit_query_returns_results():
    """Query function returns data."""
    assert True

def test_performance_pool_growth():
    """Pool grows without bound on repeated calls."""
    assert True
'''

        _setup_mocks(mock_docker, mock_ollama, test_code, "1 passed, 2 failed in 0.3s", exit_code=1)
        vet_result = _run_vet(mock_docker, mock_ollama, buggy_code, "db_pool.py")

        assert vet_result.failed == 2
        assert vet_result.passed == 1
        perf_tests = [tc for tc in vet_result.test_cases if tc.category == "performance"]
        assert len(perf_tests) >= 1

    @patch("codevet.sandbox.docker")
    @patch("codevet.vetter.ollama")
    def test_battle_19_async_blocking_io(self, mock_ollama: MagicMock, mock_docker: MagicMock) -> None:
        """Async function that blocks the event loop with sync I/O."""
        buggy_code = '''\
import asyncio
import requests  # sync library in async code!

async def fetch_data(url):
    response = requests.get(url)  # Bug: blocks event loop!
    return response.json()

async def fetch_all(urls):
    tasks = [fetch_data(url) for url in urls]
    return await asyncio.gather(*tasks)
'''

        test_code = '''\
import pytest

def test_security_blocking_io_in_async():
    """requests.get() blocks the event loop in async function."""
    import inspect
    # Detect sync call in async function pattern
    code = "async def fetch_data(url):\\n    response = requests.get(url)"
    assert "requests.get" in code
    assert "async def" in code  # Bug: sync IO in async

def test_unit_fetch_data_returns_json():
    """fetch_data returns parsed JSON."""
    assert True

def test_performance_blocking_gather():
    """gather with blocking calls serializes instead of parallelizing."""
    assert True
'''

        _setup_mocks(mock_docker, mock_ollama, test_code, "1 passed, 2 failed in 0.2s", exit_code=1)
        vet_result = _run_vet(mock_docker, mock_ollama, buggy_code, "fetcher.py")

        assert vet_result.failed == 2
        assert vet_result.passed == 1

    @patch("codevet.sandbox.docker")
    @patch("codevet.vetter.ollama")
    def test_battle_20_cache_race_condition(self, mock_ollama: MagicMock, mock_docker: MagicMock) -> None:
        """Race condition in cache read-then-write."""
        buggy_code = '''\
_cache = {}

def get_or_compute(key, compute_fn):
    if key in _cache:  # Check
        return _cache[key]
    # Race: another thread could write between check and set
    result = compute_fn()
    _cache[key] = result  # Set
    return result
'''

        test_code = '''\
import pytest

def test_security_cache_race_condition():
    """Read-then-write without lock is a TOCTOU race."""
    cache = {}
    key = "test"
    # Simulating the non-atomic check-then-set
    if key not in cache:
        cache[key] = "value1"
    assert cache[key] == "value1"  # Works single-threaded

def test_unit_cache_hit():
    """Cached value is returned on second call."""
    assert True

def test_edge_none_compute_result():
    """None return value should be cached."""
    assert True
'''

        _setup_mocks(mock_docker, mock_ollama, test_code, "2 passed, 1 failed in 0.2s", exit_code=1)
        vet_result = _run_vet(mock_docker, mock_ollama, buggy_code, "cache.py")

        assert vet_result.failed == 1
        assert vet_result.passed == 2


# ===================================================================
# Category 5: API & Network Bugs (tests 21-25)
# ===================================================================


class TestBattleAPINetwork:
    """API and network bugs from AI-generated code."""

    @patch("codevet.sandbox.docker")
    @patch("codevet.vetter.ollama")
    def test_battle_21_no_429_retry(self, mock_ollama: MagicMock, mock_docker: MagicMock) -> None:
        """HTTP retry logic that doesn't handle 429 rate limiting."""
        buggy_code = '''\
import requests
import time

def fetch_with_retry(url, max_retries=3):
    for attempt in range(max_retries):
        try:
            response = requests.get(url)
            if response.status_code == 200:
                return response.json()
            # Bug: doesn't check for 429 or Retry-After header
            if response.status_code >= 500:
                time.sleep(2 ** attempt)
                continue
            return None
        except requests.RequestException:
            time.sleep(2 ** attempt)
    return None
'''

        test_code = '''\
import pytest

def test_unit_retry_on_500():
    """500 errors trigger retry."""
    assert True

def test_security_no_429_handling():
    """429 rate limit is not handled - no Retry-After check."""
    status_codes_handled = {200, 500, 501, 502, 503}
    assert 429 not in status_codes_handled  # Bug: 429 not handled

def test_edge_zero_retries():
    """Zero retries should still try once."""
    assert True
'''

        _setup_mocks(mock_docker, mock_ollama, test_code, "1 passed, 2 failed in 0.2s", exit_code=1)
        vet_result = _run_vet(mock_docker, mock_ollama, buggy_code, "http_client.py")

        assert vet_result.failed == 2
        assert vet_result.passed == 1

    @patch("codevet.sandbox.docker")
    @patch("codevet.vetter.ollama")
    def test_battle_22_webhook_no_signature(self, mock_ollama: MagicMock, mock_docker: MagicMock) -> None:
        """Webhook handler that doesn't validate signature."""
        buggy_code = '''\
from flask import Flask, request, jsonify

app = Flask(__name__)

@app.route("/webhook", methods=["POST"])
def handle_webhook():
    # Bug: no signature validation! Anyone can POST
    data = request.get_json()
    event_type = data.get("type")
    if event_type == "payment.completed":
        process_payment(data["payload"])
        return jsonify({"status": "ok"}), 200
    return jsonify({"status": "ignored"}), 200

def process_payment(payload):
    pass
'''

        test_code = '''\
import pytest

def test_security_webhook_no_signature_check():
    """Webhook accepts any POST without signature validation."""
    # No hmac.compare_digest or signature header check
    code = """
    data = request.get_json()
    event_type = data.get("type")
    """
    assert "signature" not in code.lower()
    assert "hmac" not in code.lower()

def test_unit_payment_completed_processed():
    """payment.completed event is processed."""
    assert True

def test_edge_missing_type_key():
    """Missing 'type' key should not crash."""
    assert True
'''

        _setup_mocks(mock_docker, mock_ollama, test_code, "1 passed, 2 failed in 0.2s", exit_code=1)
        vet_result = _run_vet(mock_docker, mock_ollama, buggy_code, "webhook.py")

        assert vet_result.failed == 2
        assert vet_result.passed == 1
        assert any(tc.category == "security" for tc in vet_result.test_cases)

    @patch("codevet.sandbox.docker")
    @patch("codevet.vetter.ollama")
    def test_battle_23_200_on_error(self, mock_ollama: MagicMock, mock_docker: MagicMock) -> None:
        """REST endpoint that returns 200 on error (should be 4xx/5xx)."""
        buggy_code = '''\
from flask import Flask, jsonify, request

app = Flask(__name__)

@app.route("/api/users/<int:user_id>")
def get_user(user_id):
    users = {1: "Alice", 2: "Bob"}
    user = users.get(user_id)
    if user is None:
        return jsonify({"error": "User not found"}), 200  # Bug: should be 404!
    return jsonify({"name": user}), 200
'''

        test_code = '''\
import pytest

def test_unit_existing_user_200():
    """Existing user returns 200."""
    assert True

def test_security_missing_user_returns_200():
    """Missing user returns 200 instead of 404 - masks errors."""
    # Status code 200 for not-found is a bug
    expected_status = 404
    actual_status = 200
    assert actual_status != expected_status  # Bug confirmed

def test_edge_negative_user_id():
    """Negative user_id should return 400."""
    assert True
'''

        _setup_mocks(mock_docker, mock_ollama, test_code, "1 passed, 2 failed in 0.2s", exit_code=1)
        vet_result = _run_vet(mock_docker, mock_ollama, buggy_code, "api.py")

        assert vet_result.failed == 2
        assert vet_result.passed == 1

    @patch("codevet.sandbox.docker")
    @patch("codevet.vetter.ollama")
    def test_battle_24_graphql_n_plus_1(self, mock_ollama: MagicMock, mock_docker: MagicMock) -> None:
        """GraphQL resolver with N+1 query problem."""
        buggy_code = '''\
def resolve_users_with_posts(db):
    users = db.query("SELECT * FROM users")  # 1 query
    result = []
    for user in users:
        # Bug: N additional queries, one per user!
        posts = db.query(f"SELECT * FROM posts WHERE user_id = {user['id']}")
        result.append({"user": user, "posts": posts})
    return result
'''

        test_code = '''\
import pytest

def test_performance_n_plus_1_queries():
    """N+1 query pattern detected - should use JOIN or batch."""
    n_users = 100
    total_queries = 1 + n_users  # 1 for users + N for posts
    assert total_queries == 101  # Unacceptable query count

def test_unit_users_with_posts_structure():
    """Returns list of user+posts dicts."""
    assert True

def test_edge_no_users():
    """Empty users table returns empty list."""
    assert True
'''

        _setup_mocks(mock_docker, mock_ollama, test_code, "1 passed, 2 failed in 0.3s", exit_code=1)
        vet_result = _run_vet(mock_docker, mock_ollama, buggy_code, "resolvers.py")

        assert vet_result.failed == 2
        assert vet_result.passed == 1
        perf_tests = [tc for tc in vet_result.test_cases if tc.category == "performance"]
        assert len(perf_tests) >= 1

    @patch("codevet.sandbox.docker")
    @patch("codevet.vetter.ollama")
    def test_battle_25_websocket_no_disconnect(self, mock_ollama: MagicMock, mock_docker: MagicMock) -> None:
        """WebSocket handler that doesn't handle disconnect."""
        buggy_code = '''\
connected_clients = set()

async def websocket_handler(websocket):
    connected_clients.add(websocket)
    async for message in websocket:
        for client in connected_clients:
            await client.send(message)
    # Bug: if client disconnects with error, it stays in connected_clients
    # No try/finally to remove from set
'''

        test_code = '''\
import pytest

def test_security_no_disconnect_cleanup():
    """Disconnected clients stay in set - memory leak and send errors."""
    clients = set()
    clients.add("ws1")
    clients.add("ws2")
    # Simulate disconnect without cleanup
    assert len(clients) == 2  # ws2 should have been removed

def test_unit_broadcast_to_clients():
    """Message is sent to all connected clients."""
    assert True

def test_edge_empty_client_set():
    """No clients connected, no sends."""
    assert True
'''

        _setup_mocks(mock_docker, mock_ollama, test_code, "1 passed, 2 failed in 0.2s", exit_code=1)
        vet_result = _run_vet(mock_docker, mock_ollama, buggy_code, "ws_handler.py")

        assert vet_result.failed == 2
        assert vet_result.passed == 1


# ===================================================================
# Category 6: Type & Logic Bugs (tests 26-30)
# ===================================================================


class TestBattleTypeLogic:
    """Type and logic bugs that AI coding tools commonly introduce."""

    @patch("codevet.sandbox.docker")
    @patch("codevet.vetter.ollama")
    def test_battle_26_isinstance_misses_subclass(self, mock_ollama: MagicMock, mock_docker: MagicMock) -> None:
        """isinstance check that misses subclasses (using type() instead)."""
        buggy_code = '''\
class Animal:
    def speak(self):
        return "..."

class Dog(Animal):
    def speak(self):
        return "Woof"

def process_animal(obj):
    if type(obj) == Animal:  # Bug: type() misses Dog and other subclasses
        return obj.speak()
    raise TypeError("Not an animal")
'''

        test_code = '''\
import pytest

def test_unit_animal_type_check():
    """type() == misses subclasses, isinstance() is correct."""
    class Animal: pass
    class Dog(Animal): pass
    dog = Dog()
    assert type(dog) != Animal  # Bug: Dog IS an Animal but type() misses it
    assert isinstance(dog, Animal)  # isinstance works correctly

def test_unit_base_animal_works():
    """Base Animal passes type check."""
    assert True

def test_edge_none_input():
    """None input should raise TypeError."""
    assert True
'''

        _setup_mocks(mock_docker, mock_ollama, test_code, "1 passed, 2 failed in 0.2s", exit_code=1)
        vet_result = _run_vet(mock_docker, mock_ollama, buggy_code, "animals.py")

        assert vet_result.failed == 2
        assert vet_result.passed == 1

    @patch("codevet.sandbox.docker")
    @patch("codevet.vetter.ollama")
    def test_battle_27_mutable_default_dict_get(self, mock_ollama: MagicMock, mock_docker: MagicMock) -> None:
        """Dictionary .get() with mutable default."""
        buggy_code = '''\
def get_user_settings(users_db, user_id):
    defaults = {"theme": "light", "notifications": []}
    settings = users_db.get(user_id, defaults)
    return settings

def add_notification(users_db, user_id, notification):
    settings = get_user_settings(users_db, user_id)
    settings["notifications"].append(notification)  # Mutates shared default!
    return settings
'''

        test_code = '''\
import pytest

def test_unit_mutable_default_shared():
    """Mutable default dict is shared across calls."""
    defaults = {"notifications": []}
    s1 = defaults
    s1["notifications"].append("alert1")
    s2 = defaults  # Same object!
    assert "alert1" in s2["notifications"]  # Bug: shared mutation

def test_unit_get_settings_returns_dict():
    """Returns a dict with expected keys."""
    assert True

def test_edge_empty_users_db():
    """Empty DB returns defaults."""
    assert True
'''

        _setup_mocks(mock_docker, mock_ollama, test_code, "1 passed, 2 failed in 0.2s", exit_code=1)
        vet_result = _run_vet(mock_docker, mock_ollama, buggy_code, "settings.py")

        assert vet_result.failed == 2
        assert vet_result.passed == 1

    @patch("codevet.sandbox.docker")
    @patch("codevet.vetter.ollama")
    def test_battle_28_late_binding_closure(self, mock_ollama: MagicMock, mock_docker: MagicMock) -> None:
        """List comprehension with late binding closure."""
        buggy_code = '''\
def create_multipliers(n):
    return [lambda x: x * i for i in range(n)]
    # Bug: all lambdas capture the same `i` variable (late binding)
    # create_multipliers(4)[0](10) returns 30, not 0!
'''

        test_code = '''\
import pytest

def test_unit_late_binding_closure():
    """All lambdas use the final value of i due to late binding."""
    multipliers = [lambda x: x * i for i in range(4)]
    results = [m(10) for m in multipliers]
    # Bug: all return 30 (i=3 at call time), not [0, 10, 20, 30]
    assert results == [30, 30, 30, 30]  # Late binding confirmed

def test_unit_first_multiplier():
    """First multiplier should multiply by 0."""
    assert True

def test_edge_zero_range():
    """create_multipliers(0) returns empty list."""
    assert True
'''

        _setup_mocks(mock_docker, mock_ollama, test_code, "1 passed, 2 failed in 0.2s", exit_code=1)
        vet_result = _run_vet(mock_docker, mock_ollama, buggy_code, "multipliers.py")

        assert vet_result.failed == 2
        assert vet_result.passed == 1

        confidence = score_fix(vet_result, '{"score": 20, "reasoning": "Late binding closure bug"}')
        assert confidence.score < 45
        assert confidence.grade == "F"

    @patch("codevet.sandbox.docker")
    @patch("codevet.vetter.ollama")
    def test_battle_29_generator_consumed_twice(self, mock_ollama: MagicMock, mock_docker: MagicMock) -> None:
        """Generator that's consumed twice (empty second iteration)."""
        buggy_code = '''\
def get_active_users(users):
    return (u for u in users if u.get("active"))  # Generator, not list!

def count_and_list_active(users):
    active = get_active_users(users)
    count = sum(1 for _ in active)  # Consumes the generator
    names = [u["name"] for u in active]  # Bug: generator already exhausted!
    return count, names
'''

        test_code = '''\
import pytest

def test_unit_generator_exhausted_second_use():
    """Generator produces nothing on second iteration."""
    gen = (x for x in [1, 2, 3])
    first_pass = list(gen)
    second_pass = list(gen)  # Empty!
    assert first_pass == [1, 2, 3]
    assert second_pass == []  # Bug: generator exhausted

def test_unit_count_active_correct():
    """Count matches number of active users."""
    assert True

def test_edge_no_active_users():
    """No active users returns (0, [])."""
    assert True
'''

        _setup_mocks(mock_docker, mock_ollama, test_code, "1 passed, 2 failed in 0.2s", exit_code=1)
        vet_result = _run_vet(mock_docker, mock_ollama, buggy_code, "users.py")

        assert vet_result.failed == 2
        assert vet_result.passed == 1

    @patch("codevet.sandbox.docker")
    @patch("codevet.vetter.ollama")
    def test_battle_30_missing_wraps_decorator(self, mock_ollama: MagicMock, mock_docker: MagicMock) -> None:
        """Decorator that loses function metadata (missing @wraps)."""
        buggy_code = '''\
import time

def timing_decorator(func):
    def wrapper(*args, **kwargs):  # Bug: no @functools.wraps(func)
        start = time.time()
        result = func(*args, **kwargs)
        elapsed = time.time() - start
        print(f"{func.__name__} took {elapsed:.2f}s")
        return result
    return wrapper

@timing_decorator
def process_data(data):
    """Process the input data and return results."""
    return [x * 2 for x in data]
'''

        test_code = '''\
import pytest

def test_unit_missing_wraps_loses_metadata():
    """Without @wraps, decorated function loses __name__ and __doc__."""
    import time

    def timing_decorator(func):
        def wrapper(*args, **kwargs):
            return func(*args, **kwargs)
        return wrapper

    @timing_decorator
    def my_func():
        """My docstring."""
        pass

    assert my_func.__name__ == "wrapper"  # Bug: should be "my_func"
    assert my_func.__doc__ is None  # Bug: docstring lost

def test_unit_process_data_works():
    """process_data doubles values."""
    assert True

def test_edge_empty_data():
    """Empty list input returns empty list."""
    assert True
'''

        _setup_mocks(mock_docker, mock_ollama, test_code, "1 passed, 2 failed in 0.2s", exit_code=1)
        vet_result = _run_vet(mock_docker, mock_ollama, buggy_code, "decorators.py")

        assert vet_result.failed == 2
        assert vet_result.passed == 1

        confidence = score_fix(vet_result, '{"score": 35, "reasoning": "Missing @wraps loses metadata"}')
        assert confidence.score < 55
        assert confidence.grade in ("D", "F")


# ===================================================================
# Cross-cutting: pipeline integration assertions
# ===================================================================


class TestBattlePipelineIntegrity:
    """Verify pipeline components work together correctly across battle tests."""

    def test_parse_pytest_output_various_formats(self) -> None:
        """parse_pytest_output handles different pytest summary formats."""
        assert parse_pytest_output("5 passed in 0.3s") == (5, 0, 0)
        assert parse_pytest_output("3 passed, 2 failed in 0.5s") == (3, 2, 0)
        assert parse_pytest_output("1 passed, 1 failed, 1 error in 0.4s") == (1, 1, 1)
        assert parse_pytest_output("no tests ran") == (0, 0, 0)
        assert parse_pytest_output("") == (0, 0, 0)

    def test_confidence_score_ranges(self) -> None:
        """Confidence scoring produces correct grades for battle-test ranges."""
        # All failing -> low score
        low = calculate_confidence(pass_rate=0.33, critique_score=0.2)
        assert low.score < 50
        assert low.grade in ("D", "F")

        # Mixed -> medium score
        mid = calculate_confidence(pass_rate=0.66, critique_score=0.5)
        assert 40 <= mid.score <= 75

        # All passing with good critique -> high score
        high = calculate_confidence(pass_rate=1.0, critique_score=0.9)
        assert high.score >= 90
        assert high.grade == "A"

    def test_combine_test_cases_deduplicates_imports(self) -> None:
        """combine_test_cases doesn't duplicate import lines."""
        cases = [
            GeneratedTest(name="test_a", code="import pytest\ndef test_a():\n    assert True\n", category="unit"),
            GeneratedTest(name="test_b", code="import pytest\ndef test_b():\n    assert True\n", category="unit"),
        ]
        combined = combine_test_cases(cases)
        import_count = combined.count("import pytest")
        assert import_count == 1, f"Expected 1 'import pytest', got {import_count}"
