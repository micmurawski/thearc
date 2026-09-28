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

## Publish explicitly

Local builds and CI do not publish anything. Serve `site/` using a static host,
or choose GitHub Pages. For Pages, first configure `site_url` in `mkdocs.yml`
with the intended public URL, then run:

```sh
python -m mkdocs gh-deploy --strict
```

That command pushes generated documentation to `gh-pages`. Configure repository
Pages settings to serve the root of that branch. Run it only when ready to publish;
see the [MkDocs deployment guide](https://www.mkdocs.org/user-guide/deploying-your-docs/).
