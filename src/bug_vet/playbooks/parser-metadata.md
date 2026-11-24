# Playbook: Parser Metadata-as-Data & Deserialization Guards

## Trigger Signals
- Character-set trimming like `.strip("`")`, `.lstrip("`")`, `.rstrip("~")` used to remove Markdown code fences — destroys trailing inline code spans (`Handle None in parse()`).
- Code-fence regexes hardcoding triple backticks ```` ``` ```` without matching the opening fence character and length via backreferences — breaks nested 4-backtick fences and `~~~` fences per CommonMark rules.
- Event/line-ending terminator tables containing LF/CRLF pairs (`\n\n`, `\r\n\r\n`) while omitting CR-only (`\r\r`) or mixed line-ending pairs (`\r\n\n`, `\n\r\n`).
- Parse + serialize module compiling unescaped structural marker regexes (such as Markdown footnotes `[^1]`) without escape lookbehinds `(?<!\\)` or escaping on write.
- Deserializers (`from_dict`, `loads`, `parse`) calling `.get("key", {}).items()` without verifying the present value is a `Mapping` — payloads like `{"key": null}` or `{"key": []}` crash with `AttributeError` instead of raising the typed deserialization error.
- Lossless compression or table codecs converting container-shaped strings (`"[1, 2]"`, `'{"a": 1}'`) into live lists/dicts instead of preserving `str`.

## Adversarial Corpus & Probe Recipe
Test every parser/codec/deserializer with:
- Wrapping 3-backtick fence whose body ends with inline `` `code` ``.
- Nested 4-backtick block ```` ````markdown ```` containing inner ```` ``` ```` lines and `~~~` tilde blocks.
- CR-only (`\r\r`) and mixed (`\r\n\n`, `\n\r\n`) SSE/framing boundaries, plus split-chunk trailing `\r`.
- Body prose containing literal `[^1]` and consecutive `[^7], [^8]` tokens alongside real footnote definitions.
- Malformed mappings with `{"components": null}`, `{"components": []}`, and `{"components": "bad"}`.
- Round-trip invariant: `parse(serialize(doc)) == doc` and `type(restored) is type(original)`.

## Mandatory Falsification & Pitfalls
1. **Confusing line prefixes with state machines.** Unified diff parsers that count `---`/`+++` headers as changed lines or strip headers heuristically miscount across GitLab/Gerrit/CodeCommit formats. Use hunk headers (`@@ -a,b +c,d @@`) or an explicit fence/state tracker.
2. **Probing internal helpers without public reachability.** Confirm the parser or deserializer is called along a public entrypoint rather than dead compatibility code.
3. **Dropping existing whitespace/separators silently.** Check that unescaped legacy documents keep their original separators intact during round-trip fixes.

## Verification Checklist
- Adversarial corpus triggers failure on pre-fix code and passes cleanly after fix.
- Existing format fixtures (plain text, empty document, valid structures) remain unchanged.
