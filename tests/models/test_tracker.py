from thearc.models import MDFile, SectionChange, SectionDiffResult

BEFORE_MD = """---
title: Initial Spec
---

Introductory notes.

# Section One
Content of section one.

## Subsection 1A
Content of 1A.

# Section Two
Content of section two.
"""

AFTER_MD = """---
title: Initial Spec
---

Introductory notes.

# Section One
Updated content of section one.

## Subsection 1A
Content of 1A.

# Section Three
Brand new section three.
"""

def test_mdfile_initialization_and_indexing(tmp_path):
    file_path = tmp_path / "test_doc.md"
    file_path.write_text(BEFORE_MD, encoding="utf-8")

    md = MDFile(path=file_path)
    assert md.file_path == file_path
    assert "section-one" in md.indexed_sections
    assert "section-one/subsection-1a" in md.indexed_sections
    assert "section-two" in md.indexed_sections

    sec_one = md.get_section("section-one")
    assert sec_one is not None
    assert sec_one.title == "Section One"
    assert sec_one.content == "Content of section one."


def test_mdfile_compare():
    md_before = MDFile(text=BEFORE_MD)
    md_after = MDFile(text=AFTER_MD)

    diff: SectionDiffResult = md_before.compare(md_after)

    assert diff.has_changes is True
    
    # Verify Added Sections
    assert len(diff.added_sections) == 1
    assert diff.added_sections[0].id == "section-three"

    # Verify Removed Sections
    assert len(diff.removed_sections) == 1
    assert diff.removed_sections[0].id == "section-two"

    # Verify Modified Sections
    assert len(diff.modified_sections) == 1
    mod_change: SectionChange = diff.modified_sections[0]
    assert mod_change.section_id == "section-one"
    assert mod_change.is_content_changed is True
    assert "Updated content of section one." in mod_change.new_content
    assert "--- a/section-one" in mod_change.diff_text

    # Verify Unchanged Sections
    assert len(diff.unchanged_sections) == 1
    assert diff.unchanged_sections[0].id == "section-one/subsection-1a"


def test_mdfile_modify_and_save(tmp_path):
    file_path = tmp_path / "spec.md"
    file_path.write_text(BEFORE_MD, encoding="utf-8")

    md = MDFile(path=file_path)

    # Modify existing section
    success = md.update_section("Section One", content="Newly updated section one content.")
    assert success is True

    # Add new section
    md.add_section("Section Four", content="Content for section four.")

    # Remove section two
    md.remove_section("Section Two")

    # Save to original path
    saved_path = md.save()
    assert saved_path == file_path

    # Re-parse saved file and check updates
    md_reloaded = MDFile(path=saved_path)
    assert md_reloaded.get_section("Section One").content == "Newly updated section one content."
    assert md_reloaded.get_section("Section Four") is not None
    assert md_reloaded.get_section("Section Two") is None


def test_mdfile_save_to_new_path(tmp_path):
    md = MDFile(text=BEFORE_MD)
    target_path = tmp_path / "new_output.md"

    saved_path = md.save(path=target_path)
    assert saved_path == target_path
    assert target_path.exists()
    assert "# Section One" in target_path.read_text(encoding="utf-8")
