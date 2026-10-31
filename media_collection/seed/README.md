# seed/

Optional. If `media.db` exists here it is copied to `/data/media.db` the first time
the add-on starts (and merged into an existing library on every start, idempotently).

It is git-ignored on purpose: the database holds a real household collection. To use
your own, either

- drop your `media.db` here before installing, or
- leave it empty and import your exports through **Library admin → Import a CSV**
  (Blu-ray.com / iTunes style CSVs, staged for review before they land).

The app itself is unaffected either way — an empty seed simply means an empty
catalogue to start from.
