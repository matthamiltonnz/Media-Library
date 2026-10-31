-- seed.example.sql — sample household, safe to run against a fresh DB only.

INSERT INTO users (username, display_name, password_hash) VALUES
    ('matt',  'Matt',  NULL),
    ('sarah', 'Sarah', NULL),
    ('kids',  'Kids',  NULL);

INSERT INTO titles (name, sort_name, release_year, tmdb_id) VALUES
    ('Percy Jackson: Sea of Monsters', 'Percy Jackson: Sea of Monsters', 2013, 76203),
    ('Fast Five',                      'Fast Five',                      2011, 51497),
    ('Polar Bears 3D: Ice Bear',       'Polar Bears 3D: Ice Bear',       2012, NULL);

-- Matt owns the 3D copy, Sarah owns the standard Blu-ray, Kids own the DVD.
INSERT INTO items (title_id, owner_id, format, edition, upc_ean, visibility)
VALUES (1, 1, 'bluray_3d', NULL,        '9321337150512', 'shared'),
       (2, 2, 'bluray',    NULL,        '9342457090823', 'shared'),
       (3, 1, 'bluray_3d', NULL,        '9317731094156', 'shared'),
       (3, 3, 'dvd',       '2-Disc',    NULL,            'shared'),
       (2, 1, 'digital',   'iTunes',    NULL,            'private');
