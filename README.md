# WebFixer

**Autonomous Website Auditing & Repair Skill for Claude Code**

WebFixer is a Claude Code skill designed to autonomously inspect, audit, debug, and repair websites. It combines browser automation, visual inspection, security analysis, and functional testing to identify website problems and fix them directly in the codebase.

## Features

- **Website Crawling** — Discover and inspect internal pages, routes, and navigation.
- **Visual Inspection** — Capture screenshots and identify UI inconsistencies, layout defects, and design problems.
- **Responsive Testing** — Detect layout issues across desktop, tablet, and mobile viewports.
- **Functionality Testing** — Test navigation, forms, buttons, interactions, and user workflows.
- **Security Auditing** — Identify potential vulnerabilities, insecure configurations, and unsafe application behavior.
- **Accessibility Auditing** — Detect accessibility problems and usability barriers.
- **Performance Analysis** — Identify slow pages, inefficient resources, and rendering bottlenecks.
- **Automated Repairs** — Fix identified issues directly in the source code while preserving the existing architecture and design system.
- **Regression Testing** — Re-test affected functionality and capture screenshots after modifications.
- **Iterative Improvement** — Repeat inspection, repair, and verification until remaining issues are resolved or clearly documented.

## Workflow

1. Analyze the project architecture, framework, dependencies, and available development environment.
2. Discover website routes and important interactive states.
3. Crawl the website and capture screenshots.
4. Inspect visual appearance, functionality, accessibility, security, and performance.
5. Prioritize issues based on severity and impact.
6. Implement appropriate fixes in the source code.
7. Re-run browser tests, inspections, and screenshots.
8. Verify repairs and detect regressions.
9. Continue iterating until the website reaches a stable, verified state.
10. Report remaining issues, limitations, and completed fixes.

## Principles

- Fix root causes rather than applying superficial patches.
- Preserve existing design systems, components, architecture, and conventions.
- Avoid unnecessary redesigns or unrelated code modifications.
- Never claim a fix is verified without actually testing it.
- Respect authentication, authorization, and application security boundaries.
- Perform security testing only on authorized environments.
- Avoid destructive actions, production data modifications, or risky security tests without explicit authorization.
- Prefer minimal, maintainable, and production-quality changes.

## Requirements

WebFixer can leverage:

- Claude Code
- Playwright
- Browser automation capabilities
- Project-specific testing tools
- Static analysis and security testing tools when available

## Philosophy

**Discover. Inspect. Audit. Repair. Verify. Repeat.**

WebFixer transforms Claude Code from a development assistant into an autonomous website quality assurance and repair agent.
