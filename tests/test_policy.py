from pr_shepherd.domain import PullRequest, FileChange, CheckRun, Review
from pr_shepherd.config import ShepherdConfig
from pr_shepherd.policy import PolicyEngine, match_path

def test_match_path():
    assert match_path("auth/login.py", "auth/**") is True
    assert match_path("src/auth/login.py", "auth/**") is False
    assert match_path("db/migrations/001_init.sql", "**/*.sql") is True
    assert match_path("db/migrations/001_init.sql", "db/migrations/*.sql") is True
    assert match_path("db/migrations/nested/001_init.sql", "db/migrations/*.sql") is False

def test_policy_human_driving_override():
    config = ShepherdConfig()
    config.auto_merge_enabled = True
    engine = PolicyEngine(config)

    pr = PullRequest(
        number=1, title="Test PR", body="", state="open", author="bot",
        labels=["human-driving"], changed_files=[], comments=[], reviews=[],
        check_runs=[], head_sha="123", base_sha="456"
    )
    should_skip, is_merge_safe, reasons = engine.evaluate(pr)
    assert should_skip is True
    assert is_merge_safe is False
    assert "human-driving" in reasons[0]

def test_policy_merge_safe():
    config = ShepherdConfig()
    config.auto_merge_enabled = True
    engine = PolicyEngine(config)

    # Low risk PR, green checks
    pr = PullRequest(
        number=1, title="Test PR", body="", state="open", author="bot",
        labels=[], 
        changed_files=[FileChange(filename="src/utils.py", additions=10, deletions=5)],
        comments=[], reviews=[],
        check_runs=[CheckRun(name="test", status="completed", conclusion="success")],
        head_sha="123", base_sha="456"
    )
    should_skip, is_merge_safe, reasons = engine.evaluate(pr)
    assert should_skip is False
    assert is_merge_safe is True
    assert len(reasons) == 0

def test_policy_holds():
    config = ShepherdConfig()
    config.auto_merge_enabled = True
    engine = PolicyEngine(config)

    # Case 1: Sensitive Path
    pr_sensitive = PullRequest(
        number=1, title="Test PR", body="", state="open", author="bot",
        labels=[], 
        changed_files=[FileChange(filename="auth/secret.py", additions=1, deletions=1)],
        comments=[], reviews=[], check_runs=[], head_sha="123", base_sha="456"
    )
    _, is_merge_safe, reasons = engine.evaluate(pr_sensitive)
    assert is_merge_safe is False
    assert any("sensitive path" in r for r in reasons)

    # Case 2: DB Migrations
    pr_migration = PullRequest(
        number=2, title="Test PR", body="", state="open", author="bot",
        labels=[], 
        changed_files=[FileChange(filename="src/migrations/schema_info.txt", additions=10, deletions=0)],
        comments=[], reviews=[], check_runs=[], head_sha="123", base_sha="456"
    )
    _, is_merge_safe, reasons = engine.evaluate(pr_migration)
    assert is_merge_safe is False
    assert any("DB migrations" in r for r in reasons)

    # Case 3: Reviewer Requested Changes
    pr_block = PullRequest(
        number=3, title="Test PR", body="", state="open", author="bot",
        labels=[], changed_files=[], comments=[], 
        reviews=[Review(id=1, user="alice", state="CHANGES_REQUESTED", body="No")],
        check_runs=[], head_sha="123", base_sha="456"
    )
    _, is_merge_safe, reasons = engine.evaluate(pr_block)
    assert is_merge_safe is False
    assert any("outstanding changes requested" in r for r in reasons)

def test_policy_pending_checks_block_merge():
    config = ShepherdConfig()
    config.auto_merge_enabled = True
    engine = PolicyEngine(config)

    # PR with a pending check (status="in_progress")
    pr = PullRequest(
        number=4, title="Test PR", body="", state="open", author="bot",
        labels=[], 
        changed_files=[FileChange(filename="src/utils.py", additions=1, deletions=1)],
        comments=[], reviews=[],
        check_runs=[
            CheckRun(name="test", status="completed", conclusion="success"),
            CheckRun(name="build", status="in_progress", conclusion=None)
        ],
        head_sha="123", base_sha="456"
    )
    _, is_merge_safe, _ = engine.evaluate(pr)
    assert is_merge_safe is False
