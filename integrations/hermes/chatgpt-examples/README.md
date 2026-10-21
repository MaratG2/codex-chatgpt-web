# Educational text examples in the existing ChatGPT Web runtime

This plugin adds `chatgpt_example` (`prepare` → `record`). It neither calls a
model API nor launches a browser, chat, task, subagent or scheduled execution.
The current Web model generates `answer_text` on the continuation after prepare.
The handler records that structured tool-call output with the source snapshots,
prompt, model, bound runtime thread, timestamps and content hashes.

For an actual follow-up, prepare a second prompt with `previous_record` pointing
to the first saved JSON. Do not replace the first answer or invent an error.
Records live under `products/<product-id>/examples/runtime/<case-id>/`.

This is a text-generation demonstration in the existing agent context. It is
not an isolated student session, not an observation of the ChatGPT UI and not
independent QA. The current context may contain course information. State this
in internal QA; Critic and Learner QA still run separately and inspect outcomes.
UI claims require relevant official documentation or actual UI evidence. An
unavailable separate browser does not invalidate this working text route.

Enable `chatgpt-examples` in the Hermes plugins list; new workers load it without
restarting existing workers. Source is maintained in the launcher fork under
`integrations/hermes/chatgpt-examples/`. No live tests were run during deployment.
