# Manga Translation Context

This context defines identity and storage language for manga records and translation work.

## Language

**Record ID**:
Stable identifier for a persisted record and the canonical identity exposed by the API.
_Avoid_: folder name, title, composite key

**Manga Group**:
A named collection of manga page records.
_Avoid_: folder, directory

**Manga Page**:
One original or translated page record with its metadata and file-backed assets.
_Avoid_: image file

**Batch Item**:
One translation job record within a translation batch.
_Avoid_: upload, file

**Asset Folder**:
The file-backed storage location for a manga page’s images and metadata; it is a lookup alias, not record identity.
_Avoid_: page ID
