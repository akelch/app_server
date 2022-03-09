# Changelog

This file documents any relevant changes.

## [0.7.3] - 2022-03-09
- disabled gRPC fork support
- increased default timeout to 60 Sesconds

## [0.7.2] - 2021-09-17
- we now reuse gunicorn porn
- added timeout till 502 error 

## [0.7.0] - 2021-09-17
- clean up entire source
- dropping of several functions, to make the source code easier to read
- Improved PEP8-conformity
- Check on runtime version and correct Python version
- Use entrypoint from app.yaml, with additional variables

## [0.6.1] - 2021-09-16
- Improved version number maintenance
- Better handling for regex-statics 

## [0.6.0] - 2021-09-15
- url handlers can now contain regex patterns

## [0.5.8] - 2021-09-15
- added some GAE environment variables.

## [0.5.7] - 2021-09-09
- gunicorn now reloads on file change
- added a start delay preventing connection errors

## [0.5.5] - 2021-08-24
- corrected Readme

## [0.5.4] - 2021-08-24
- werkzeug response code is now 501
- proxy chunk_size fixed
- werkzeug server is now threaded

## [0.5.3] - 2021-08-12
- first test release
