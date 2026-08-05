<?php
declare(strict_types=1);

/**
 * AstroGoblinSearch — web UI (PHP).
 *
 * Reads the SQLite database written by the Python indexer (data/transcripts.db)
 * and serves a full-text search over every transcript. Drop this directory into
 * any PHP-capable web server (Apache mod_php, nginx + php-fpm, etc.) — no Python
 * runtime is needed on the web host. Only the indexer (cron) runs Python.
 */

// --- Configuration ---------------------------------------------------------
const CHANNEL_NAME = 'Astrogoblin';
// Path to the database produced by the Python indexer. Override with the
// AGS_DB_PATH environment variable when deploying outside this layout.
function db_path(): string {
    return getenv('AGS_DB_PATH') ?: dirname(__DIR__) . '/data/transcripts.db';
}

// --- Database --------------------------------------------------------------
function get_db(): PDO {
    $path = db_path();
    if (!file_exists($path)) {
        throw new RuntimeException('Database not found at ' . $path);
    }
    $pdo = new PDO('sqlite:' . $path);
    $pdo->setAttribute(PDO::ATTR_ERRMODE, PDO::ERRMODE_EXCEPTION);
    $pdo->setAttribute(PDO::ATTR_DEFAULT_FETCH_MODE, PDO::FETCH_ASSOC);
    // Wait briefly if the indexer is mid-write instead of erroring.
    $pdo->exec('PRAGMA busy_timeout = 3000');
    return $pdo;
}

// --- Helpers ---------------------------------------------------------------
/** Build a safe FTS5 query: alphanumeric tokens ANDed as prefix terms. */
function build_fts_query(string $q): ?string {
    preg_match_all('/[A-Za-z0-9]+/', $q, $m);
    $tokens = array_map('strtolower', $m[0]);
    if (!$tokens) {
        return null;
    }
    return implode(' ', array_map(fn(string $t): string => $t . '*', $tokens));
}

function fmt_time(float $seconds): string {
    $s = (int)round($seconds);
    $h = intdiv($s, 3600);
    $rem = $s % 3600;
    $m = intdiv($rem, 60);
    $sec = $rem % 60;
    return $h !== 0 ? sprintf('%d:%02d:%02d', $h, $m, $sec) : sprintf('%d:%02d', $m, $sec);
}

function yt_link(string $id, float $start): string {
    return 'https://www.youtube.com/watch?v=' . $id . '&t=' . (int)round($start) . 's';
}

function thumb_url(string $id): string {
    return 'https://i.ytimg.com/vi/' . $id . '/hqdefault.jpg';
}

/** Escape then turn the chr(1)/chr(2) FTS match markers into <mark> tags. */
function render_fts_snippet(string $snippet): string {
    $esc = htmlspecialchars($snippet, ENT_QUOTES, 'UTF-8');
    return str_replace([chr(1), chr(2)], ['<mark>', '</mark>'], $esc);
}

/** LIKE-path highlight: window around the first token hit, all tokens marked. */
function render_like_snippet(string $text, array $tokens): string {
    $lower = mb_strtolower($text);
    $best = false;
    foreach ($tokens as $t) {
        $p = mb_strpos($lower, $t);
        if ($p !== false && ($best === false || $p < $best)) {
            $best = $p;
        }
    }
    $start = max(0, ($best === false ? 0 : $best) - 30);
    $snip = mb_substr($text, $start, 90);
    $esc = htmlspecialchars($snip, ENT_QUOTES, 'UTF-8');
    foreach ($tokens as $t) {
        $esc = preg_replace('/(' . preg_quote($t, '/') . ')/i', '<mark>$1</mark>', $esc) ?? $esc;
    }
    return $esc;
}

/** Absolute base URL of this site (e.g. https://search.astrogoblin.jammaloo.com).
 *  Social-media crawlers do not resolve relative URLs, so og:image / twitter:image
 *  must be absolute. Derived from the request so it's correct in any deployment. */
function og_base_url(): string {
    $https = (!empty($_SERVER['HTTPS']) && $_SERVER['HTTPS'] !== 'off')
        || (($_SERVER['SERVER_PORT'] ?? '') == 443)
        || (($_SERVER['HTTP_X_FORWARDED_PROTO'] ?? '') === 'https');
    $scheme = $https ? 'https' : 'http';
    $host   = $_SERVER['HTTP_HOST'] ?? $_SERVER['SERVER_NAME'] ?? 'localhost';
    return $scheme . '://' . $host;
}

// --- Search ----------------------------------------------------------------
/**
 * @return array{rows: array<int, array>, fallback: bool}
 *     rows each carry: video_id, start, end, snippet_html, youtube_id, title,
 *     upload_date — ordered newest upload first, then by segment start.
 */
function search(PDO $db, string $query): array {
    preg_match_all('/[A-Za-z0-9]+/', $query, $m);
    $tokens = array_map('strtolower', $m[0]);
    if (!$tokens) {
        return ['rows' => [], 'fallback' => false];
    }

    $order = 'ORDER BY v.upload_date DESC, v.id DESC, s.start ASC';
    $from  = 'FROM segments s JOIN videos v ON v.id = s.video_id';

    // Preferred path: the FTS5 index the Python indexer maintains.
    try {
        $sql = "SELECT s.id, s.video_id, s.start, s.end, s.text, "
             . "snippet(segments_fts, 0, char(1), char(2), '…', 16) AS snippet, "
             . "v.youtube_id, v.title, v.upload_date "
             . "FROM segments_fts JOIN segments s ON s.id = segments_fts.rowid "
             . "JOIN videos v ON v.id = s.video_id "
             . "WHERE segments_fts MATCH :q AND v.status = 'done' " . $order;
        $st = $db->prepare($sql);
        $st->execute([':q' => build_fts_query($query)]);
        $rows = [];
        foreach ($st as $r) {
            $rows[] = [
                'video_id' => (int)$r['video_id'],
                'start' => (float)$r['start'],
                'snippet_html' => render_fts_snippet((string)$r['snippet']),
                'youtube_id' => $r['youtube_id'],
                'title' => $r['title'],
                'upload_date' => $r['upload_date'],
            ];
        }
        return ['rows' => $rows, 'fallback' => false];
    } catch (Throwable $e) {
        // FTS5 unavailable on this host — fall back to a portable LIKE scan.
    }

    $where = "WHERE v.status = 'done'";
    $params = [];
    $i = 0;
    foreach ($tokens as $t) {
        $where .= " AND s.clean_text LIKE :p$i";
        $params[":p$i"] = '%' . $t . '%';
        $i++;
    }
    $sql = "SELECT s.video_id, s.start, s.end, s.text, v.youtube_id, v.title, v.upload_date "
         . "$from $where $order";
    $st = $db->prepare($sql);
    $st->execute($params);
    $rows = [];
    foreach ($st as $r) {
        $rows[] = [
            'video_id' => (int)$r['video_id'],
            'start' => (float)$r['start'],
            'snippet_html' => render_like_snippet((string)$r['text'], $tokens),
            'youtube_id' => $r['youtube_id'],
            'title' => $r['title'],
            'upload_date' => $r['upload_date'],
        ];
    }
    return ['rows' => $rows, 'fallback' => true];
}

function get_stats(PDO $db): array {
    $count = fn(string $where): int => (int)$db->query("SELECT COUNT(*) FROM videos $where")->fetchColumn();
    $last = $db->query("SELECT title FROM videos WHERE status = 'done' ORDER BY indexed_at DESC, id DESC LIMIT 1")->fetchColumn();
    return [
        'total' => $count(''),
        'done' => $count("WHERE status = 'done'"),
        'pending' => $count("WHERE status = 'pending'"),
        'failed' => $count("WHERE status = 'failed'"),
        'last' => $last === false ? null : $last,
    ];
}

// --- Request handling ------------------------------------------------------
$query = isset($_GET['q']) ? trim($_GET['q']) : '';
$results = [];        // grouped: [['youtube_id','title','upload_date','matches'=>[...]]]
$match_count = 0;
$fallback = false;
$db_error = null;

try {
    $db = get_db();
    $stats = get_stats($db);
    if ($query !== '') {
        $searchResult = search($db, $query);
        $fallback = $searchResult['fallback'];
        $videos = [];
        foreach ($searchResult['rows'] as $r) {
            $vid = $r['video_id'];
            if (!isset($videos[$vid])) {
                $videos[$vid] = [
                    'youtube_id' => $r['youtube_id'],
                    'title' => $r['title'],
                    'upload_date' => $r['upload_date'],
                    'matches' => [],
                ];
            }
            $videos[$vid]['matches'][] = [
                'time' => fmt_time($r['start']),
                'link' => yt_link($r['youtube_id'], $r['start']),
                'snippet_html' => $r['snippet_html'],
            ];
            $match_count++;
        }
        $results = array_values($videos);
    }
} catch (Throwable $e) {
    $db_error = $e->getMessage();
    $stats = ['total' => 0, 'done' => 0, 'pending' => 0, 'failed' => 0, 'last' => null];
}

function e(?string $s): string {
    return htmlspecialchars($s ?? '', ENT_QUOTES, 'UTF-8');
}
?>
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>astrogoblin video search</title>
  <meta property="og:title" content="astrogoblin video search">
  <meta property="og:description" content="Search the spoken content of every Astrogoblin video — matches link straight to the moment in the video where the words were said.">
  <meta property="og:type" content="website">
  <meta property="og:url" content="<?= e(og_base_url()) ?>/">
  <meta property="og:site_name" content="<?= e(CHANNEL_NAME) ?>">
  <meta property="og:image" content="<?= e(og_base_url()) ?>/logo-og.png">
  <meta property="og:image:alt" content="<?= e(CHANNEL_NAME) ?> logo">
  <meta property="og:image:type" content="image/png">
  <meta property="og:image:width" content="1200">
  <meta property="og:image:height" content="1200">
  <meta name="twitter:card" content="summary">
  <meta name="twitter:title" content="astrogoblin video search">
  <meta name="twitter:description" content="Search the spoken content of every Astrogoblin video — matches link straight to the moment where the words were said.">
  <meta name="twitter:image" content="<?= e(og_base_url()) ?>/logo-og.png">
  <meta name="twitter:image:alt" content="<?= e(CHANNEL_NAME) ?> logo">
  <style>
    :root {
      --bg: #0f1117; --panel: #171a23; --panel-2: #1f2330; --border: #2a2f3d;
      --text: #e6e8ee; --muted: #8b93a7; --accent: #ff3b3b; --accent-2: #ff7878;
      --mark-bg: #f5d061; --mark-text: #1a1a1a; --link: #6cb6ff;
    }
    * { box-sizing: border-box; }
    body { margin: 0; font-family: ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, sans-serif; background: var(--bg); color: var(--text); line-height: 1.55; }
    header.top { border-bottom: 1px solid var(--border); padding: 22px 24px; background: linear-gradient(180deg, #161922, var(--bg)); }
    .wrap { max-width: 880px; margin: 0 auto; padding: 0 20px; }
header.top .brand { display: flex; align-items: center; gap: 12px; margin-bottom: 4px; }
header.top .brand .logo { width: 44px; height: 44px; flex: 0 0 auto; display: block; border-radius: 9px; }
header.top h1 { margin: 0; font-size: 1.45rem; letter-spacing: -0.01em; }
    .meta { color: var(--muted); font-size: 0.9rem; }
    .meta b { color: var(--text); font-weight: 600; }
    .search { padding: 30px 0 8px; }
    form.s { display: flex; gap: 10px; }
    form.s input { flex: 1; padding: 13px 15px; font-size: 1rem; border-radius: 10px; border: 1px solid var(--border); background: var(--panel); color: var(--text); }
    form.s input:focus { outline: none; border-color: var(--accent-2); }
    form.s button { padding: 13px 22px; font-size: 1rem; font-weight: 600; cursor: pointer; border: none; border-radius: 10px; background: var(--accent); color: #fff; }
    form.s button:hover { background: var(--accent-2); }
    .hint { color: var(--muted); font-size: 0.85rem; margin-top: 8px; }
    .result-summary { color: var(--muted); font-size: 0.95rem; margin: 22px 0 4px; }
    .result-summary b { color: var(--text); }
    .video { margin: 22px 0; border: 1px solid var(--border); border-radius: 12px; background: var(--panel); overflow: hidden; }
    .video-head { display: flex; align-items: center; gap: 14px; padding: 12px 14px; background: var(--panel-2); border-bottom: 1px solid var(--border); }
    .video-head .thumb { flex: 0 0 auto; width: 120px; height: 68px; border-radius: 7px; overflow: hidden; background: #000; }
    .video-head .thumb img { width: 100%; height: 100%; object-fit: cover; display: block; }
    .video-head .thumb.no-thumb { visibility: hidden; }
    .video-head .head-text { display: flex; flex-direction: column; gap: 3px; min-width: 0; flex: 1 1 auto; }
    .video-head .title { font-weight: 600; }
    .video-head .title a { color: var(--text); text-decoration: none; }
    .video-head .title a:hover { color: var(--link); }
    .video-head .info { color: var(--muted); font-size: 0.82rem; }
    .match { padding: 12px 16px; border-top: 1px solid var(--border); display: grid; grid-template-columns: auto 1fr; gap: 14px; align-items: start; }
    .match:first-of-type { border-top: none; }
    .ts a { display: inline-block; min-width: 64px; text-align: center; padding: 5px 10px; border-radius: 7px; background: var(--accent); color: #fff; font-weight: 600; text-decoration: none; font-variant-numeric: tabular-nums; font-size: 0.85rem; }
    .ts a:hover { background: var(--accent-2); }
    .snip { color: #cdd2de; }
    mark { background: var(--mark-bg); color: var(--mark-text); border-radius: 3px; padding: 0 2px; }
    .empty, .error { color: var(--muted); padding: 40px 0; text-align: center; }
    .error { color: var(--accent-2); }
    footer { color: var(--muted); font-size: 0.8rem; text-align: center; padding: 40px 0 30px; }
    @media (max-width: 560px) { .match { grid-template-columns: 1fr; } .video-head .thumb { width: 96px; height: 54px; } }
  </style>
</head>
<body>
  <header class="top">
    <div class="wrap">
      <div class="brand">
        <img src="logo.png" alt="" width="44" height="44" class="logo">
        <h1>astrogoblin video search</h1>
      </div>
<?php if ($db_error !== null): ?>
      <div class="meta">⚠ Database error: <?= e($db_error) ?></div>
<?php else: ?>
      <div class="meta">
        <b><?= (int)$stats['done'] ?></b> of <?= (int)$stats['total'] ?> videos indexed
        · <?= (int)$stats['pending'] ?> pending<?php if (!empty($stats['failed'])): ?> · <?= (int)$stats['failed'] ?> failed<?php endif; ?><?php if ($stats['last']): ?> · last indexed: <b><?= e($stats['last']) ?></b><?php endif; ?>
      </div>
<?php endif; ?>
    </div>
  </header>

  <main class="wrap">
    <section class="search">
      <form class="s" method="get" action="">
        <input type="text" name="q" value="<?= e($query) ?>" placeholder="Search spoken words across every video…" autofocus autocomplete="off">
        <button type="submit">Search</button>
      </form>
      <div class="hint">Matches link straight to the moment in the video where it was said.<?php if ($fallback): ?> (running in compatibility/LIKE mode on this server)<?php endif; ?></div>
    </section>

<?php if ($query !== ''): ?>
<?php if ($results): ?>
      <div class="result-summary"><b><?= $match_count ?></b> match(es) in <b><?= count($results) ?></b> video(s) for “<?= e($query) ?>”</div>
<?php foreach ($results as $v): ?>
      <article class="video">
        <div class="video-head">
          <a class="thumb" href="https://www.youtube.com/watch?v=<?= e($v['youtube_id']) ?>" target="_blank" rel="noopener">
            <img src="<?= e(thumb_url($v['youtube_id'])) ?>" alt="" loading="lazy" referrerpolicy="no-referrer" onerror="this.closest('.thumb').classList.add('no-thumb')">
          </a>
          <div class="head-text">
            <span class="title"><a href="https://www.youtube.com/watch?v=<?= e($v['youtube_id']) ?>" target="_blank" rel="noopener"><?= e($v['title']) ?></a></span>
            <span class="info"><?= e($v['upload_date'] ?: '—') ?> · <?= count($v['matches']) ?> match(es)</span>
          </div>
        </div>
<?php foreach ($v['matches'] as $m): ?>
        <div class="match">
          <span class="ts"><a href="<?= e($m['link']) ?>" target="_blank" rel="noopener"><?= e($m['time']) ?></a></span>
          <span class="snip">…<?= $m['snippet_html'] ?>…</span>
        </div>
<?php endforeach; ?>
      </article>
<?php endforeach; ?>
<?php else: ?>
      <div class="empty">No matches found for “<?= e($query) ?>”.</div>
<?php endif; ?>
<?php endif; ?>

    <footer>Searches every indexed transcript. New videos are picked up daily.</footer>
  </main>
</body>
</html>
