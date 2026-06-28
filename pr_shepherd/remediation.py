import re
from typing import Optional, Tuple
from pr_shepherd.domain import PullRequest, Comment
from pr_shepherd.config import ShepherdConfig
from pr_shepherd.github_client import GitHubClient
from pr_shepherd.llm_client import LLMClient

# Label that must be present on the PR for the remediation engine to auto-commit
# code changes.  Without this label, suggestions are surfaced as comments only.
APPLY_SUGGESTIONS_LABEL = "shepherd:apply-suggestions"

class RemediationEngine:
    def __init__(self, config: ShepherdConfig, github_client: GitHubClient, llm_client: LLMClient):
        self.config = config
        self.gh = github_client
        self.llm = llm_client

    def parse_suggestion(self, comment_body: str) -> Optional[str]:
        """Extracts the suggestion code block from a review comment if present."""
        match = re.search(r"```suggestion\s*\n([\s\S]*?)```", comment_body)
        if match:
            return match.group(1).strip()
        return None

    def check_template_status(self, body: str) -> Tuple[bool, list]:
        """Verifies if the PR description contains the required commitperclip sections."""
        required_sections = ["Thinking Path", "What Changed", "Verification", "Risks", "Model Used"]
        missing = []
        for sec in required_sections:
            # Look for heading style headers
            pattern = rf"##\s*{re.escape(sec)}"
            if not re.search(pattern, body, re.IGNORECASE):
                missing.append(sec)
        
        # Check for dedup checkbox
        # Matches: [ ] or [x] with search for similar PRs
        dedup_pattern = r"\[[ xX]\]\s*I\s+have\s+searched\s+for\s+similar\s+PRs"
        if not re.search(dedup_pattern, body, re.IGNORECASE):
            missing.append("Dedup Checkbox")
            
        return len(missing) == 0, missing

    def remediate(self, pr: PullRequest) -> bool:
        """
        Executes the remediation flow.
        Returns True if any changes were made, False otherwise.
        """
        remediation_performed = False

        # 1. Format / Template Gate Remediation
        # Check if the commitperclip check has failed or if sections are missing
        has_commitperclip_failure = any(
            "commitperclip" in run.name.lower() and run.conclusion in ("failure", "action_required")
            for run in pr.check_runs
        )
        
        template_ok, missing_sections = self.check_template_status(pr.body)
        if has_commitperclip_failure or not template_ok:
            print(f"[Remediator] Format gate failure found. Missing sections: {missing_sections}")
            
            # Generate a new template body
            diff_text = "\n".join([f.patch for f in pr.changed_files if f.patch])
            # Simulated or actual commit messages
            commit_history = f"PR Title: {pr.title}\nAuthor: {pr.author}"
            
            new_sections = self.llm.generate_pr_description(diff_text, commit_history)
            
            # Combine the existing body with the new template sections
            updated_body = pr.body + "\n\n" + new_sections
            self.gh.update_pr_description(pr.number, updated_body)
            self.gh.post_comment(
                pr.number,
                "🤖 **PR-Shepherd Auto-Remediation**: Corrected missing template sections required by `commitperclip`."
            )
            remediation_performed = True

        # 2. Parse and Surface Reviewer Nits
        # Determine whether auto-commit is allowed (requires explicit opt-in label)
        auto_commit_allowed = APPLY_SUGGESTIONS_LABEL in pr.labels

        if not auto_commit_allowed:
            print(f"[Remediator] Label '{APPLY_SUGGESTIONS_LABEL}' not present — "
                  "suggestions will be surfaced as comments only (no auto-commit).")

        for comment in pr.comments:
            # Validate author trust level
            if comment.association not in ("OWNER", "COLLABORATOR", "MEMBER"):
                print(f"[Remediator] Skipping suggestion from untrusted user association: {comment.association}")
                continue

            suggestion = self.parse_suggestion(comment.body)
            if suggestion is not None and comment.path and comment.line is not None:
                print(f"[Remediator] Found suggestion comment from {comment.user} on {comment.path}:{comment.line}")

                if not auto_commit_allowed:
                    # Post a comment describing the suggestion instead of committing
                    self.gh.post_comment(
                        pr.number,
                        f"🤖 **PR-Shepherd Suggestion Detected** (from @{comment.user} on "
                        f"`{comment.path}:{comment.line}`):\n\n"
                        f"```suggestion\n{suggestion}\n```\n\n"
                        f"To allow automatic application, add the `{APPLY_SUGGESTIONS_LABEL}` "
                        f"label to this PR."
                    )
                    remediation_performed = True
                    continue

                # Auto-commit path (label is present)
                commit_msg = f"style: apply reviewer suggestion from @{comment.user} on {comment.path}"
                
                if self.config.dry_run:
                    print(f"[Remediator] [Dry-Run] Would apply suggestion to {comment.path}: {suggestion!r}")
                else:
                    try:
                        # Fetch full file contents from GitHub
                        file_content = self.gh.get_file_contents(comment.path, pr.head_sha)
                        lines = file_content.splitlines(keepends=True)
                        
                        if 0 < comment.line <= len(lines):
                            # Replace target line (preserving newline boundaries if any)
                            has_newline = lines[comment.line - 1].endswith("\n")
                            lines[comment.line - 1] = suggestion + ("\n" if has_newline or not lines[comment.line - 1] else "")
                            updated_content = "".join(lines)
                            
                            # Commit the complete updated content
                            self.gh.commit_file_change(pr.number, comment.path, updated_content, commit_msg)
                            print(f"[Remediator] Committed updated file {comment.path} with reviewer suggestion.")
                        else:
                            print(f"[Remediator] Target line {comment.line} out of bounds for {comment.path} (total lines: {len(lines)})")
                            continue
                    except Exception as e:
                        print(f"[Remediator] Error applying suggestion: {e}")
                        continue
                        
                self.gh.post_comment(
                    pr.number,
                    f"🤖 **PR-Shepherd Suggestion Applied**: Automatically applied the suggested fix to `{comment.path}`."
                )
                remediation_performed = True
                
        return remediation_performed
