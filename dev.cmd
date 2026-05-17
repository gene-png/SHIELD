@echo off
REM SHIELD - Windows dev entrypoint. No `make` required.
REM
REM   dev.cmd help                     show targets
REM   dev.cmd demo                     first-run: build, up, migrate, seed
REM   dev.cmd up                       start db + keycloak + app
REM   dev.cmd down                     stop containers (keeps volumes)
REM   dev.cmd nuke                     stop AND remove volumes (DESTROYS DATA)
REM   dev.cmd logs                     tail app logs
REM   dev.cmd migrate                  flask db upgrade
REM   dev.cmd seed                     load demo data
REM   dev.cmd reset                    drop schema, re-migrate, reseed
REM   dev.cmd test                     run pytest
REM   dev.cmd shell                    bash inside the app container
REM   dev.cmd vendor-attack            fetch full MITRE catalog into DB
REM
REM Mirrors the Makefile, but POSIX `make` is not needed.

setlocal

set COMPOSE=docker compose --env-file .env -f compose/docker-compose.yml -f compose/docker-compose.dev.yml

if "%1"==""              goto help
if "%1"=="help"          goto help
if "%1"=="up"            goto up
if "%1"=="down"          goto down
if "%1"=="nuke"          goto nuke
if "%1"=="logs"          goto logs
if "%1"=="migrate"       goto migrate
if "%1"=="seed"          goto seed
if "%1"=="reset"         goto reset
if "%1"=="demo"          goto demo
if "%1"=="test"          goto test
if "%1"=="shell"         goto shell
if "%1"=="vendor-attack" goto vendor_attack

echo Unknown target: %1
echo Run `dev.cmd help` for the list.
exit /b 2

:help
echo SHIELD dev targets (Windows alternative to `make`):
echo   demo            build + up + migrate + seed (first-run)
echo   up              start db + keycloak + app
echo   down            stop containers (keeps volumes)
echo   nuke            stop AND remove volumes (DESTROYS DATA)
echo   logs            tail app logs
echo   migrate         flask db upgrade
echo   seed            load demo data (Acme Co + 75 capabilities + 33 MITRE + 3 projects + 3 users)
echo   reset           drop schema, re-migrate, reseed
echo   test            run pytest in the app container
echo   shell           bash inside the app container
echo   vendor-attack   fetch the full MITRE ATT&CK Enterprise catalog (~222 techniques)
goto end

:up
%COMPOSE% up -d --wait db keycloak app
goto end

:down
%COMPOSE% down
goto end

:nuke
%COMPOSE% down -v
goto end

:logs
%COMPOSE% logs -f app
goto end

:migrate
%COMPOSE% exec app flask --app wsgi:app db upgrade
goto end

:seed
%COMPOSE% exec app flask --app wsgi:app seed
goto end

:reset
%COMPOSE% exec app flask --app wsgi:app reset-db --yes
%COMPOSE% exec app flask --app wsgi:app db upgrade
%COMPOSE% exec app flask --app wsgi:app seed
goto end

:demo
%COMPOSE% build
%COMPOSE% up -d --wait db keycloak
%COMPOSE% up -d --wait app
%COMPOSE% exec app flask --app wsgi:app db upgrade
%COMPOSE% exec app flask --app wsgi:app seed
echo.
echo ================================================================
echo  SHIELD is up:    http://localhost:8000
echo  Keycloak admin:  http://localhost:8080  (admin / admin)
echo  Users:           admin@demo / client@demo / reviewer@demo   pw: demo
echo ================================================================
goto end

:test
%COMPOSE% exec app pytest -q
goto end

:shell
%COMPOSE% exec app /bin/bash
goto end

:vendor_attack
%COMPOSE% exec app flask --app wsgi:app vendor-attack
goto end

:end
endlocal
