# Security policy

## Supported versions

Log Tracer is in its 0.x series. Security fixes are made in the latest released version only.

| Version | Supported |
| ------- | --------- |
| 0.1.x   | Yes       |

## Reporting a vulnerability

**Do not open a public issue for a security problem.**

Report it privately through GitHub: go to the [Security tab](https://github.com/carlobaratta/log-tracer/security) of the repository and choose **Report a vulnerability**. GitHub's documentation explains [how private reporting works](https://docs.github.com/en/code-security/security-advisories/guidance-on-reporting-and-writing-information-about-vulnerabilities/privately-reporting-a-security-vulnerability).

Please include:

- the Log Tracer version (`log-tracer --version`), Python version and operating system;
- the command you ran;
- a minimal log or configuration file that triggers the problem, with any real data removed;
- what happens and what impact you think it has.

Log Tracer is maintained by one person in their spare time. You can expect an acknowledgment within 7 days and an assessment within 30 days. Once a fix is released, the advisory will be published with credit to you, unless you prefer to stay anonymous.

## Scope

Log Tracer reads log files that may come from untrusted sources, so a crafted input file is the main threat. These are in scope:

- a log or configuration file that makes Log Tracer print terminal control sequences unescaped, for example to rewrite the terminal or hide text;
- a crafted input that makes Log Tracer write outside its state directory or the `--output` file, or execute code;
- an input that uses memory or time far out of proportion to its size;
- any network access made by Log Tracer itself.

These are not security problems:

- a malformed input that ends with a documented error and exit code, as listed in SPEC.md §9;
- the content of a report revealing data that was already in the logs you analyzed;
- problems in a dependency that do not affect Log Tracer. Please report those to the dependency.
