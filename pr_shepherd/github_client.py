import os
from typing import List, Optional
from github import Github
from pr_shepherd.domain import PullRequest, FileChange, Comment, Review, CheckRun
from pr_shepherd.config import ShepherdConfig

class GitHubClient:
    def __init__(self, config: ShepherdConfig, repo_name: str = ""):
        self.config = config
        self.repo_name = repo_name or os.environ.get("GITHUB_REPOSITORY", "octocat/Hello-World")
        self.token = config.github_token
        self.g = Github(self.token) if self.token and not config.dry_run else None

    def get_pull_request(self, pr_number: int) -> PullRequest:
        """Fetch full details of a pull request from GitHub API, or return a mock in dry-run mode."""
        if self.config.dry_run or not self.g:
            return self._get_mock_pull_request(pr_number)

        repo = self.g.get_repo(self.repo_name)
        pr = repo.get_pull(pr_number)
        
        # 1. Fetch file changes
        changed_files = []
        for f in pr.get_files():
            changed_files.append(FileChange(
                filename=f.filename,
                additions=f.additions,
                deletions=f.deletions,
                patch=f.patch
            ))

        # 2. Fetch comments (both issue and pull request review comments)
        comments = []
        for c in pr.get_issue_comments():
            comments.append(Comment(
                id=c.id,
                body=c.body,
                user=c.user.login,
                association=c.author_association
            ))
        for c in pr.get_comments():
            comments.append(Comment(
                id=c.id,
                body=c.body,
                user=c.user.login,
                path=c.path,
                line=c.line,
                in_reply_to_id=c.in_reply_to_id,
                association=c.author_association
            ))

        # 3. Fetch reviews
        reviews = []
        for r in pr.get_reviews():
            reviews.append(Review(
                id=r.id,
                user=r.user.login,
                state=r.state,
                body=r.body
            ))

        # 4. Fetch check runs
        check_runs = []
        commit = repo.get_commit(pr.head.sha)
        for run in commit.get_check_runs():
            check_runs.append(CheckRun(
                name=run.name,
                status=run.status,
                conclusion=run.conclusion,
                details_url=run.details_url
            ))

        # 5. Build and return PullRequest domain object
        return PullRequest(
            number=pr.number,
            title=pr.title,
            body=pr.body or "",
            state=pr.state,
            author=pr.user.login,
            labels=[l.name for l in pr.get_labels()],
            changed_files=changed_files,
            comments=comments,
            reviews=reviews,
            check_runs=check_runs,
            head_sha=pr.head.sha,
            base_sha=pr.base.sha,
            mergeable=pr.mergeable
        )

    def post_comment(self, pr_number: int, body: str) -> None:
        print(f"[GitHub Client] Posting comment on PR #{pr_number}: {body}")
        if self.config.dry_run or not self.g:
            return
        repo = self.g.get_repo(self.repo_name)
        pr = repo.get_pull(pr_number)
        pr.create_issue_comment(body)

    def add_label(self, pr_number: int, label: str) -> None:
        print(f"[GitHub Client] Adding label '{label}' to PR #{pr_number}")
        if self.config.dry_run or not self.g:
            return
        repo = self.g.get_repo(self.repo_name)
        pr = repo.get_pull(pr_number)
        pr.add_to_labels(label)

    def remove_label(self, pr_number: int, label: str) -> None:
        print(f"[GitHub Client] Removing label '{label}' from PR #{pr_number}")
        if self.config.dry_run or not self.g:
            return
        repo = self.g.get_repo(self.repo_name)
        pr = repo.get_pull(pr_number)
        try:
            pr.remove_from_labels(label)
        except Exception:
            pass  # Label might not exist

    def update_pr_description(self, pr_number: int, body: str) -> None:
        print(f"[GitHub Client] Updating description of PR #{pr_number}")
        if self.config.dry_run or not self.g:
            return
        repo = self.g.get_repo(self.repo_name)
        pr = repo.get_pull(pr_number)
        pr.edit(body=body)

    def commit_file_change(self, pr_number: int, filename: str, content: str, commit_message: str) -> None:
        """Commits a change directly to the PR's head branch."""
        print(f"[GitHub Client] Committing changes to '{filename}' for PR #{pr_number}")
        if self.config.dry_run or not self.g:
            return
        repo = self.g.get_repo(self.repo_name)
        pr = repo.get_pull(pr_number)
        ref = repo.get_git_ref(f"heads/{pr.head.ref}")
        
        # Read current file to get sha
        try:
            contents = repo.get_contents(filename, ref=pr.head.ref)
            sha = contents.sha
            repo.update_file(filename, commit_message, content, sha, branch=pr.head.ref)
        except Exception:
            # File might be new
            repo.create_file(filename, commit_message, content, branch=pr.head.ref)

    def merge_pull_request(self, pr_number: int) -> bool:
        print(f"[GitHub Client] Squashing and merging PR #{pr_number}")
        if self.config.dry_run or not self.g:
            return True
        repo = self.g.get_repo(self.repo_name)
        pr = repo.get_pull(pr_number)
        status = pr.merge(merge_method="squash")
        return status.merged

    def _get_mock_pull_request(self, pr_number: int) -> PullRequest:
        """Helper returning mock PR data when in dry-run or no tokens are provided."""
        return PullRequest(
            number=pr_number,
            title="fix(server): wake creator agent when board user rejects request_confirmation",
            body="An agent PR that lacks template sections.",
            state="open",
            author="gemini-coder-agent",
            labels=[],
            changed_files=[
                FileChange(filename="server/main.py", additions=10, deletions=5, patch="@@ -10,5 +10,10 @@")
            ],
            comments=[
                Comment(
                    id=101,
                    body="Could you please use a different variable name here?\n```suggestion\nnew_var = 10\n```",
                    user="reviewer-bob",
                    path="server/main.py",
                    line=12,
                    association="COLLABORATOR"
                )
            ],
            reviews=[
                Review(id=201, user="reviewer-bob", state="COMMENTED", body="Reviewing the nits")
            ],
            check_runs=[
                CheckRun(name="commitperclip", status="completed", conclusion="failure"),
                CheckRun(name="build-and-test", status="completed", conclusion="success")
            ],
            head_sha="abcdef123456",
            base_sha="123456abcdef"
        )
