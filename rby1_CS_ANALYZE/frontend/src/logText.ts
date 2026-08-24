type LogDisplayContext = {
  component?: string | null;
};

const LEADING_RBY_WALL_TIME = /^\s*\[\d{2}\/\d{2}\/\d{2}\s+\d{2}:\d{2}:\d{2}(?:\.\d+)?\]\s*/;
const LEADING_ISO_WALL_TIME = /^\s*\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?\s*/;
const LEADING_LEVEL = /^\[(?:trace|debug|info|warning|warn|error|critical)\]\s*/i;
const LEADING_RELATIVE_TIME = /^\[\d{2}:\d{2}:\d{2}(?:\.\d+)?\]\s*/;

function escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

export function confirmedLogMessage(excerpt: string, context: LogDisplayContext = {}): string {
  let message = excerpt
    .replace(LEADING_RBY_WALL_TIME, "")
    .replace(LEADING_ISO_WALL_TIME, "")
    .replace(LEADING_LEVEL, "")
    .replace(LEADING_RELATIVE_TIME, "");

  const component = context.component?.trim();
  if (component) {
    message = message.replace(new RegExp(`^\\[${escapeRegExp(component)}\\]\\s*`, "i"), "");
  }

  return message.trim();
}
