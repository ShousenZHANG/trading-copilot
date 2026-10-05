# Public code and private instance data

The repository and release archive contain reusable code, public market data,
portable documentation and explicitly synthetic test scenarios. Account identity,
personal preferences, holdings, fills, balances, research journals, credentials
and local machine settings belong only in an ignored private instance.

## Before publishing

1. Run `python scripts/privacy_audit.py` and `python scripts/check.py`. These
   inspect tracked files, including file contents, rather than trusting ignore
   rules alone. Findings identify a file, line and category without echoing the
   matched value. Synthetic examples use reserved placeholder values.
2. Review prose for real personal context. Pattern checks cannot distinguish all
   real portfolio figures from synthetic financial examples. Do not copy a real
   conversation into research documents or fixtures and label it synthetic.
3. For an instance-specific check, keep a JSON file under `data/state/` with
   `schema_version: 1` and a `literals` array of sensitive strings. Pass its path
   using `--denylist` to the privacy scanner, or `--privacy-denylist` to the
   package builder and verifier. Never commit that file or its contents. Public
   CI uses generic checks; it does not need access to private instance data.
4. Build and verify the release archive. Both steps scan its actual contents.
   Confirm the release points at the tested commit and publish only the reviewed
   ZIP, checksum and source metadata. Do not upload local run logs or databases.
5. Use a repository-scoped pseudonymous Git identity when personal contact
   details should not appear in commit metadata. Preserve third-party license
   notices and attribution. The GitHub repository owner's public account remains
   part of its URL and hosting metadata.

## Removing previously published data

Removing a working-tree file does not remove its old commits, tags, release
attachments, Actions logs or PR references. Audit those surfaces separately.
Rewrite affected branches and tags from an isolated clone, verify all rewritten
objects, and push with explicit leases so concurrent remote updates are not lost.
Retire contaminated release attachments and publish a verified replacement.

Keep local instance files outside this operation. Verify their integrity before
and after the rewrite; use `git reset --mixed` to align the index after importing
the sanitized history instead of overwriting a live working tree. Do not push
local recovery references or merge an old clone back into the sanitized history.

GitHub's internal PR references and cached old commit views may require GitHub
Support. A repository rewrite cannot retract another person's downloaded copy
or fork. Document the affected refs for the repository owner without republishing
the private values. See [GitHub's removal procedure](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/removing-sensitive-data-from-a-repository).
