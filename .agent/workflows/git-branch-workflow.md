---
description: how to commit and push changes using feature branches
---

For every set of changes, follow this git branching workflow:

1. Create a new branch from `main` with a descriptive name:
   ```
   git checkout main && git pull && git checkout -b <branch-name>
   ```
   Use a short, kebab-case branch name describing the change (e.g., `update-icon`, `fix-edge-sync`, `add-config-option`).

2. Make your changes and commit them to the branch:
   ```
   git add <files> && git commit -m "<descriptive commit message>"
   ```

3. Merge the branch back into `main` and push:
   ```
   git checkout main && git merge <branch-name> && git push
   ```

4. Delete the feature branch after merging:
   ```
   git branch -d <branch-name>
   ```
