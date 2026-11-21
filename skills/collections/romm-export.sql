SELECT JSON_OBJECT(
  'platform', p.fs_slug,
  'fs_name', r.fs_name,
  'name', r.name,
  'alt_names', JSON_MERGE_PRESERVE(
      COALESCE(JSON_EXTRACT(r.ss_metadata, '$.alternative_names'), '[]'),
      COALESCE(JSON_EXTRACT(r.igdb_metadata, '$.alternative_names'), '[]')),
  'summary', r.summary,
  'genres', JSON_EXTRACT(COALESCE(r.generated_genres, '[]'), '$'),
  'release_ms', IF(r.generated_first_release_date_unset = 1, NULL, r.generated_first_release_date),
  'developers', JSON_EXTRACT(COALESCE(r.generated_developers, '[]'), '$'),
  'publishers', JSON_EXTRACT(COALESCE(r.generated_publishers, '[]'), '$'),
  'players', r.generated_player_count,
  'rating', IF(r.generated_average_rating_unset = 1, NULL, r.generated_average_rating),
  'rating_count', r.generated_rating_count,
  'cover', r.path_cover_l,
  'screenshots', JSON_EXTRACT(COALESCE(r.path_screenshots, '[]'), '$'),
  'missing', r.missing_from_fs
)
FROM roms r JOIN platforms p ON p.id = r.platform_id
ORDER BY p.fs_slug, r.fs_name;
