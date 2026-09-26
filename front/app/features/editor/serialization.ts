import type { EditableTextBlock } from '@/types';

export function parseEditableTextBlocks(data: unknown): EditableTextBlock[] {
  if (!Array.isArray(data) || data.length === 0) return [];

  return data.map((item, idx) => ({
    id: item.id || `bubble_${idx}`,
    bubble_safe_shape:
      item.bubble_safe_shape && typeof item.bubble_safe_shape.png === 'string'
        ? item.bubble_safe_shape
        : null,
    group_members: Array.isArray(item.group_members)
      ? item.group_members
      : undefined,
    review_required:
      item.review_required === true ||
      (Array.isArray(item.layout_segments) &&
        item.layout_segments.length > 1 &&
        item.layout_segments.some(
          (segment: Record<string, unknown>) =>
            typeof segment.rendered_png !== 'string',
        )),
    review_reason:
      typeof item.review_reason === 'string' ? item.review_reason : null,
    x: Number(item.x) || 0,
    y: Number(item.y) || 0,
    width: Math.max(40, Number(item.width) || 120),
    height: Math.max(30, Number(item.height) || 80),
    lines: Array.isArray(item.lines) ? item.lines : undefined,
    cover_background: Boolean(item.cover_background),
    translation: String(item.translation || ''),
    original_text: item.original_text || '',
    font_size: Math.max(10, Number(item.font_size) || 24),
    font_family: item.font_family || "'Comic Neue', cursive, sans-serif",
    fg_color: Array.isArray(item.fg_color) ? item.fg_color : [0, 0, 0],
    bg_color: Array.isArray(item.bg_color)
      ? item.bg_color
      : [255, 255, 255],
    stroke_width:
      typeof item.stroke_width === 'number' ? item.stroke_width : 3.0,
    angle: Number(item.angle) || 0,
    direction: item.direction === 'v' ? 'v' : 'h',
    alignment: item.alignment || 'center',
    line_spacing: item.line_spacing == null ? 1.15 : Number(item.line_spacing),
    letter_spacing: Number(item.letter_spacing) || 0,
    bold: Boolean(item.bold),
    italic: Boolean(item.italic),
    layout_bounds:
      item.layout_bounds && typeof item.layout_bounds === 'object'
        ? {
            x: Number(item.layout_bounds.x) || 0,
            y: Number(item.layout_bounds.y) || 0,
            width: Math.max(40, Number(item.layout_bounds.width) || 120),
            height: Math.max(30, Number(item.layout_bounds.height) || 80),
          }
        : undefined,
    layout_segments: Array.isArray(item.layout_segments)
      ? item.layout_segments.map((segment: Record<string, unknown>) => ({
          x: Number(segment.x) || 0,
          y: Number(segment.y) || 0,
          width: Math.max(1, Number(segment.width) || 1),
          height: Math.max(1, Number(segment.height) || 1),
          text: String(segment.text || ''),
          font_size: segment.font_size ? Number(segment.font_size) : undefined,
          rendered_png:
            typeof segment.rendered_png === 'string'
              ? segment.rendered_png
              : null,
          positioned_lines: Array.isArray(segment.positioned_lines)
            ? segment.positioned_lines.map(
                (line: Record<string, unknown>) => ({
                  text: String(line.text || ''),
                  x: Number(line.x) || 0,
                  y: Number(line.y) || 0,
                }),
              )
            : undefined,
        }))
      : undefined,
  }));
}
