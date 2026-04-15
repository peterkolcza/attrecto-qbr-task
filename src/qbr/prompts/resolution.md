# Resolution Tracking Prompt — Stage B

You are an expert project analyst. Your task is to determine the resolution status of previously extracted items from an email thread.

## Instructions

For each extracted item below, determine whether it was RESOLVED within this email thread.

## Resolution Status Rules

- **resolved**: The item was clearly addressed, answered, or completed within the thread. There is explicit evidence of resolution.
- **open**: The item was raised but never addressed, or the response is still pending. No evidence of resolution in the thread.
- **ambiguous**: There is some response but it's unclear whether the item is fully resolved. Partial answers count as ambiguous.

## Rules

1. Only consider messages AFTER the item was raised (message_index > item's message_index).
2. Provide a brief rationale for your decision.
3. If resolved, provide the `resolving_message_index`.
4. Be conservative: if unsure, mark as "ambiguous" rather than "resolved".

{spotlighting_preamble}

## Email Thread: {thread_subject}
Source: {source_file}

{thread_content}

## Items to Evaluate

{items_json}

## Output

Return a JSON array with the same items, each augmented with:
- `status`: one of "open", "resolved", "ambiguous"
- `resolution_rationale`: brief explanation
- `resolving_message_index`: message index that resolves it (null if open/ambiguous)
