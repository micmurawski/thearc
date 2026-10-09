# Package releases

The `Package release` workflow tests Python 3.11 and 3.13, builds the wheel and
source distribution, validates metadata, and smoke-tests the installed wheel.
Pull requests, pushes to `main`, and manual runs validate without publishing.
Publishing a GitHub Release triggers a PyPI upload after all checks pass.

## One-time setup

1. Create the GitHub environment `pypi`. Restrict deployments to release tags
   (`v*`) and optionally require a reviewer.
2. In PyPI, add a GitHub trusted publisher with these exact values:

   | Field | Value |
   | --- | --- |
   | PyPI project | `thearc` |
   | Owner | `micmurawski` |
   | Repository | `thearc` |
   | Workflow filename | `release.yml` |
   | Environment | `pypi` |

For the first upload, register a pending publisher from your PyPI account's
publishing settings. If the project already exists, configure its publisher as
an owner. A pending publisher does not reserve a project name. No API-token secret
is needed; see [PyPI trusted publishing](https://docs.pypi.org/trusted-publishers/).

## Release from your local checkout

Update `project.version` in `pyproject.toml`, commit the intended changes, and push
them to `main`. Use a clean, reviewed commit; do not include unrelated local work.
Then create a matching tag and publish the GitHub Release:

```sh
git tag -a v0.1.0 -m "thearc 0.1.0"
git push origin v0.1.0
gh release create v0.1.0 --verify-tag --title "thearc 0.1.0" --generate-notes
```

Replace `0.1.0` with the new version. Tag names must exactly equal `v` plus the
version in `pyproject.toml`. Tagging alone does not upload to PyPI; publishing the
GitHub Release does. The protected publishing job only downloads the checked build
artifacts and uploads them; it does not check out or execute project code.

## Local validation

```sh
python -m pip install build twine
python -m build
python -m twine check --strict dist/*
```

Use a clean build directory so old distributions cannot be uploaded accidentally.
Source distributions exclude test fixtures, experiment logs, and documentation
archives. Wheels include the library and its visualization assets.

## Failed uploads

Check the `publish` job for trusted-publisher/environment configuration errors.
Correct setup and rerun a failed workflow when no files were uploaded. PyPI release
files are immutable; an already-published filename cannot be replaced. For changed
package content, increment the version and create a new release. A partial upload
needs inspection before retrying; the workflow does not silently skip existing files.

Publishing through the workflow does not use local PyPI credentials. A local
`twine upload` is a separate, credentialed publishing path and is not required here.
