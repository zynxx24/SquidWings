---
name: laravel
description: Laravel from zero to a running link — PHP and extensions, Composer, MySQL (sudo), .env, migrations, auth, Vite assets, serve, preview link, and the errors that always come up.
tasks: code, activity
triggers: laravel, artisan, php, composer, blade, livewire, inertia, filament, breeze, jetstream, eloquent, migration, migrasi, ppdb, sekolah, mysql, mariadb, phpmyadmin, xampp, lamp
tools: terminal_run, execute_command, preview_url, file_write, file_patch, read_file, style_pick, aislop_scan, run_checks
---
# Laravel, end to end

Do every step yourself. `sudo` works: when the owner saved the sudo password (Kredensial → Sistem) commands with
`sudo` run by themselves; otherwise they open in the Terminal panel for the user to type it. Never ask for a
password in the chat. Long-running things (`artisan serve`, `npm run dev`) go through `terminal_run`.

## 1. Check the machine (one command, read all of it)

```bash
php -v; composer -V; mysql --version; node -v; php -m | grep -iE "pdo_mysql|mbstring|xml|curl|zip|gd|intl|bcmath"
```

Missing pieces (Ubuntu/Debian; use the PHP version `php -v` shows, e.g. 8.3):

```bash
sudo apt-get update
sudo apt-get install -y php8.3-cli php8.3-mysql php8.3-xml php8.3-mbstring php8.3-curl php8.3-zip php8.3-gd php8.3-intl php8.3-bcmath unzip
# Composer when missing:
php -r "copy('https://getcomposer.org/installer', 'composer-setup.php');" && sudo php composer-setup.php --install-dir=/usr/local/bin --filename=composer && rm composer-setup.php
# MySQL server when missing:
sudo apt-get install -y mysql-server && sudo systemctl enable --now mysql
```

## 2. Database user (MySQL's root uses socket auth: make an app user)

```bash
sudo mysql -e "CREATE DATABASE IF NOT EXISTS ppdb CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER IF NOT EXISTS 'ppdb'@'localhost' IDENTIFIED BY 'ppdb_pass_ganti';
GRANT ALL PRIVILEGES ON ppdb.* TO 'ppdb'@'localhost'; FLUSH PRIVILEGES;"
```

No MySQL wanted (quick demo): use SQLite — `DB_CONNECTION=sqlite` and `touch database/database.sqlite`.

## 3. Create the project

```bash
composer create-project laravel/laravel ppdb --no-interaction
cd ppdb
cp -n .env.example .env && php artisan key:generate
```

Edit `.env`: `APP_NAME`, `APP_URL=http://127.0.0.1:8000`, `DB_CONNECTION=mysql`, `DB_DATABASE=ppdb`,
`DB_USERNAME=ppdb`, `DB_PASSWORD=ppdb_pass_ganti`, `APP_LOCALE=id`, `APP_TIMEZONE=Asia/Jakarta` (Laravel 11:
timezone lives in `config/app.php` `'timezone' => env('APP_TIMEZONE', 'UTC')`).

Auth when the app has users: `composer require laravel/breeze --dev && php artisan breeze:install blade --no-interaction && npm install && npm run build`.
Admin panel fast: Filament (`composer require filament/filament && php artisan filament:install --panels`).

## 4. Build features the Laravel way

- `php artisan make:model Pendaftar -mcr` (model + migration + resource controller) — then fill the migration columns.
- Validation in a Form Request (`php artisan make:request StorePendaftarRequest`), rules with messages in Indonesian.
- Routes in `routes/web.php` (`Route::resource('pendaftar', PendaftarController::class)->middleware('auth')`).
- Authorisation: policies (`php artisan make:policy PendaftarPolicy --model=Pendaftar`); check ownership, not just login.
- Uploads: `$request->file('berkas')->store('berkas', 'public')` + `php artisan storage:link`; validate `mimes:pdf,jpg,png|max:2048`.
- Blade layout `resources/views/layouts/app.blade.php` with `@vite(['resources/css/app.css','resources/js/app.js'])`;
  design with `style_pick(medium="web")` tokens in `resources/css/app.css` — not the default Laravel look.
- Seed demo data: `php artisan make:seeder` + factories so the page is never empty on first open.
- Export/print (bukti pendaftaran): `composer require barryvdh/laravel-dompdf`.

## 5. Migrate, build assets, run, link

```bash
php artisan migrate --seed --force
npm install && npm run build          # or `npm run dev` in a second terminal_run while developing
php artisan serve --host=127.0.0.1 --port=8000     # via terminal_run
```

Then `preview_url(8000)` and give the user the link. For the https preview link, trust the proxy in
`bootstrap/app.php` (Laravel 11): `->withMiddleware(function ($m) { $m->trustProxies(at: '*'); })`, and set
`ASSET_URL`/`APP_URL` to that https link if assets still load over http.

## 6. Verify

`php artisan test`, `php artisan route:list`, open the main pages through the link, submit each form once with valid
and once with invalid data, log in as two different users and confirm one cannot open the other's record (IDOR).
Then `load_skill("redhat")` for anything with login, uploads or personal data.

## Errors you will meet (and the fix)

| Error | Fix |
|---|---|
| `could not find driver` | `sudo apt-get install -y php8.3-mysql` (match `php -v`), restart serve |
| `SQLSTATE[HY000] [1045] Access denied` | wrong `DB_USERNAME`/`DB_PASSWORD` or user not created — step 2, then `php artisan config:clear` |
| `SQLSTATE[HY000] [2002] Connection refused` | MySQL not running: `sudo systemctl start mysql`; or `DB_HOST=127.0.0.1` vs `localhost` |
| `No application encryption key` | `php artisan key:generate` |
| `Vite manifest not found` | `npm install && npm run build` (or keep `npm run dev` running) |
| `The stream or file storage/logs/laravel.log could not be opened` | `chmod -R 775 storage bootstrap/cache` (inside the project folder) |
| `Class "X" not found` after adding files | `composer dump-autoload` |
| 419 Page Expired on forms | missing `@csrf` in the form, or session cookie blocked behind the link — trust proxy, `SESSION_SECURE_COOKIE=true` for https |
| Changes in `.env` ignored | `php artisan config:clear && php artisan cache:clear` |
| `Address already in use` | `--port=8001`, or `fuser -k 8000/tcp` |
| Composer memory error | `COMPOSER_MEMORY_LIMIT=-1 composer install` |

## How to think this through

Laravel fails at the edges, not in the code: a missing PHP extension, a database user that does not exist, an
`.env` the config cache still ignores, assets that were never built. So check the machine first, make the database
reachable second, and only then write features. After every step run the one command that proves it worked
(`php artisan migrate:status`, `curl -I http://127.0.0.1:8000`) before moving on, and read the whole error message —
the first line usually names the exact missing piece.
