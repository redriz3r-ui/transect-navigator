# GPX 1.1 schema fixture

`gpx-1.1.xsd` was downloaded unchanged from the official schema:
https://www.topografix.com/GPX/1/1/gpx.xsd

Documentation: https://www.topografix.com/GPX/1/1/

Used only by development tests through `xmllint --nonet --schema`. It is not
bundled with the plugin ZIP and is not required at runtime. Tests skip XSD validation
explicitly if xmllint is absent. Standard-library parsing/content tests still run.
The schema permits foreign-namespace GPX extensions with lax validation; tests
separately verify every emitted Transect Navigator extension against its source record.

SHA-256: `9e4d1988b862edbe556305b130f8f6f1b29864fefd0dc02d5dab04ccdd1f34d6`
