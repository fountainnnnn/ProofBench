function parseTableRow(line) {
  const cells = [];
  let cell = "";
  let escaped = false;

  for (const char of line.trim()) {
    if (char === "|" && !escaped) {
      cells.push(cell.trim());
      cell = "";
    } else {
      cell += char;
    }
    escaped = char === "\\" && !escaped;
    if (char !== "\\") escaped = false;
  }
  cells.push(cell.trim());

  if (cells[0] === "") cells.shift();
  if (cells.at(-1) === "") cells.pop();
  return cells;
}

function isTableDivider(cells) {
  return cells.length > 0 && cells.every((cell) => /^:?-{3,}:?$/.test(cell));
}

/**
 * Older deterministic reports inserted display names directly into Markdown
 * table rows. A name containing "|" therefore became an extra cell. Generated
 * report tables always place the candidate name in column two, so surplus cells
 * can be joined back into that column without changing narrative pipes.
 */
export function repairLegacyReportTables(markdown) {
  const lines = String(markdown || "").split("\n");

  for (let index = 0; index < lines.length - 1; index += 1) {
    if (!lines[index].trim().startsWith("|")) continue;
    const header = parseTableRow(lines[index]);
    const divider = parseTableRow(lines[index + 1]);
    if (header.length < 2 || divider.length !== header.length || !isTableDivider(divider)) continue;

    for (let rowIndex = index + 2; rowIndex < lines.length; rowIndex += 1) {
      const row = lines[rowIndex].trim();
      if (!row.startsWith("|")) break;
      const cells = parseTableRow(row);
      if (cells.length <= header.length) continue;

      const surplus = cells.length - header.length;
      const candidate = cells.slice(1, 2 + surplus).join(" \\| ");
      const repaired = [cells[0], candidate, ...cells.slice(2 + surplus)];
      lines[rowIndex] = `| ${repaired.join(" | ")} |`;
    }
  }

  return lines.join("\n");
}

/**
 * Extract the candidate-level subsections beneath "## Findings". The source
 * Markdown stays untouched for downloads and PDFs; this only gives the browser
 * enough structure to group each finding visually.
 */
export function splitReportFindings(markdown) {
  const lines = String(markdown || "").split("\n");
  const findingsHeading = lines.findIndex((line) => /^##\s+Findings\s*$/i.test(line.trim()));
  if (findingsHeading === -1) return null;

  let nextSection = lines.length;
  for (let index = findingsHeading + 1; index < lines.length; index += 1) {
    if (/^##\s+/.test(lines[index].trim())) {
      nextSection = index;
      break;
    }
  }

  const body = lines.slice(findingsHeading + 1, nextSection);
  const intro = [];
  const findings = [];
  let current = null;
  for (const line of body) {
    if (/^###\s+/.test(line.trim())) {
      if (current) findings.push(current.join("\n").trim());
      current = [line];
    } else if (current) {
      current.push(line);
    } else {
      intro.push(line);
    }
  }
  if (current) findings.push(current.join("\n").trim());
  if (findings.length === 0) return null;

  return {
    before: lines.slice(0, findingsHeading + 1).join("\n").trim(),
    intro: intro.join("\n").trim(),
    findings,
    after: lines.slice(nextSection).join("\n").trim(),
  };
}
