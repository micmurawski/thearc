# Maintain these docs

The site is built from ordinary Markdown in `docs/`. It does not import the README,
generate pages from experiment logs, or execute cookbook examples during a build.

```sh
python -m pip install -e '.[docs]'
python -m mkdocs serve
```

Open `http://127.0.0.1:8000`. To validate and build:

```sh
python -m mkdocs build --strict
```

The ignored `site/` directory contains the result. CI runs the same strict build.
Missing internal pages, anchors, and navigation entries fail validation.

## Keep one home for each topic

- `start/`: installation and one end-to-end tutorial.
- `guides/`: how to complete a task with the current public API.
- `reference/`: command and runtime details.
- `examples/`: a short map to executable recipes maintained in `cookbook/`.
- `development/`: contribution instructions and known limitations.

Add new pages to `mkdocs.yml`. Use relative links within the docs and repository
links for source files. Avoid copying entire guides into the README or cookbook.
Keep runnable examples small, label commands that invoke models, and state any
required input artifacts. The README is a separate project introduction.

Implementation plans and experiment reports belong outside `docs/`; older material
is preserved under `archive/documentation/` and is excluded from the site and search.
Do not publish credentials, local transcripts, or raw experiment output.

## Publish with GitHub Actions

In **Settings → Pages → Build and deployment**, set **Source** to **GitHub Actions**.
Do not select the source branch's `/` or `/docs` folder: those contain source files,
not the generated MkDocs site.

The `Documentation` workflow validates every push and pull request. After a
successful build on `main`, it uploads only `site/` and deploys that artifact using
GitHub Pages. Pull requests and other branches do not publish. Maintainers can
also run the workflow manually on `main` from the Actions tab. Deployments use the
`github-pages` environment and respect its protection rules.

The public URL is `https://micmurawski.github.io/thearc/`, also configured as
`site_url` in `mkdocs.yml`. If using a fork or custom domain, change that setting.
If renaming `main`, update the workflow's upload and deployment conditions.
Local `mkdocs build` and `mkdocs serve` never publish anything.

### Jekyll or Liquid errors

An error such as `Unknown tag 'extends'` in `tests/fastapi/...` means a Jekyll build
is traversing repository source rather than deploying the MkDocs output. Switch
Pages to **GitHub Actions**, push the updated workflow, and check its deployment
job. Do not edit the fixture's wiki or publish the whole repository with a root
`.nojekyll` file. This workflow does not require a `gh-pages` branch.

See [GitHub's custom Pages workflow guide](https://docs.github.com/en/pages/getting-started-with-github-pages/using-custom-workflows-with-github-pages).
