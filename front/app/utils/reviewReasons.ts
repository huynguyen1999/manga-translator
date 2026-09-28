export interface ReviewReasonCopy {
  label: string;
  guidance: string;
}

export const REVIEW_REASON_COPY: Record<string, ReviewReasonCopy> = {
  text_does_not_fit: {
    label: "Translation does not fit the bubble",
    guidance: "Shorten the translation or resize the box, then replace the preserved original.",
  },
  font_below_readability_floor: {
    label: "Font size is below the readability floor",
    guidance: "The text was scaled down too small to remain readable. Enlarge the bubble or shorten the translation.",
  },
  font_compressed_from_source: {
    label: "Font heavily compressed compared to original",
    guidance: "The font size was significantly reduced to fit. Verify readability and adjust wording if necessary.",
  },
  text_requires_emergency_compression: {
    label: "Emergency text compression required",
    guidance: "The translation required severe font reduction to fit. Shorten the wording or enlarge the bubble.",
  },
  emergency_word_split: {
    label: "Emergency word hyphenation applied",
    guidance: "A long word had to be split across lines with a hyphen. Check if the split looks natural.",
  },
  closest_valid_bubble_placement: {
    label: "Text shifted to fit bubble shape",
    guidance: "Text was placed off-center to avoid clipping against bubble borders. Reposition or resize if needed.",
  },
  no_valid_layout: {
    label: "No valid layout found within constraints",
    guidance: "Text could not be positioned without violating bubble, panel, or page boundaries. Adjust the box size or edit the text.",
  },
  "no_joint_layout: rendered text collision": {
    label: "Text collides with neighboring text",
    guidance: "This text region collided with adjacent text. Move or resize neighboring text blocks to resolve the overlap.",
  },
  no_joint_layout: {
    label: "Text collides with neighboring text",
    guidance: "This text region collided with adjacent text. Move or resize neighboring text blocks to resolve the overlap.",
  },
  uncertain_cleanup: {
    label: "Original lettering could not be safely cleared",
    guidance: "Check the background before replacing it, or accept the preserved original.",
  },
  uncertain_boundary: {
    label: "Bubble boundary is uncertain",
    guidance: "Reposition the box to align with the speech bubble, then replace the preserved original.",
  },
  translation_validation_failed: {
    label: "Translation failed validation",
    guidance: "Edit the translation until it is complete and readable, then replace the preserved original.",
  },
  low_confidence_story_boundary: {
    label: "Story boundary confidence is low",
    guidance: "Check this page against the surrounding pages before replacing the preserved original.",
  },
  response_truncated: {
    label: "Translation response was cut off",
    guidance: "The AI hit its output limit mid-response. This region was not translated in that batch. Edit the translation manually, then replace the preserved original.",
  },
  render_suppressed: {
    label: "Rendering suppressed due to layout constraints",
    guidance: "Rendering was suppressed because the text could not fit safely within bubble or panel boundaries. Adjust text or bubble size.",
  },
  invalid_frozen_layout: {
    label: "Saved layout is invalid or missing segments",
    guidance: "The saved typeset layout has no valid segments. Re-typeset or edit the bubble.",
  },
  rasterization_failed: {
    label: "Text rasterization failed",
    guidance: "An error occurred while generating the text raster. Check font settings and character support.",
  },
  "render rasterization failed": {
    label: "Text rasterization failed",
    guidance: "An error occurred while generating the text raster. Check font settings and character support.",
  },
  render_failed: {
    label: "Text rendering failed",
    guidance: "An unexpected error occurred during rendering. Re-typeset or edit the bubble.",
  },
  "Translation identical to original": {
    label: "Translation identical to original",
    guidance: "The translation matches the original Japanese text. Rendering was suppressed to preserve original lettering.",
  },
  possible_numeric_content: {
    label: "Possible numeric or symbol content",
    guidance: "The text contains numbers or symbols. Verify that the translation preserved numbers correctly.",
  },
  "final layout leaves its panel": {
    label: "Text extends outside comic panel",
    guidance: "The rendered text overflows outside its comic panel boundary. Shrink font size or reposition the box.",
  },
  "final layout leaves its bubble": {
    label: "Text overflows speech bubble shape",
    guidance: "The rendered text exceeds the detected speech bubble shape. Shorten the translation or resize the bubble.",
  },
  "final layout leaves its local placement domain": {
    label: "Text placed outside allowed domain",
    guidance: "The text extends beyond its valid placement area. Reposition the text block within the designated space.",
  },
  "final layout overlaps restored source text": {
    label: "Layout overlaps preserved original text",
    guidance: "This translated text overlaps original Japanese artwork that was preserved. Adjust position or clear original text.",
  },
  "final layout overlaps a protected speech bubble": {
    label: "Free text overlaps a speech bubble",
    guidance: "This free-text extends into a protected speech bubble area. Adjust the boundary or reposition the text.",
  },
  "rotated layout outside page bounds": {
    label: "Text extends outside page margins",
    guidance: "Rotated text extends beyond the image boundaries. Reposition it away from page edges.",
  },
};

export function getReviewReasonCopy(reason?: string | null): ReviewReasonCopy {
  if (!reason || !reason.trim()) {
    return {
      label: "Manual review required",
      guidance: "Check the preserved original and either replace it with this translation or accept the original.",
    };
  }

  const trimmed = reason.trim();
  if (REVIEW_REASON_COPY[trimmed]) {
    return REVIEW_REASON_COPY[trimmed];
  }

  // Dynamic prefix matching
  if (trimmed.startsWith("no_valid_layout:")) {
    const detail = trimmed.slice("no_valid_layout:".length).trim();
    const detailLower = detail.toLowerCase();
    let detailLabel = "No valid layout found";
    let detailGuidance = "Text could not be positioned without violating boundaries. Adjust box size or edit text.";

    if (detailLower.includes("panel")) {
      detailLabel = "Text cannot fit within comic panel";
      detailGuidance = "The text overflows the comic panel frame. Shorten the text or enlarge the box.";
    } else if (detailLower.includes("bubble")) {
      detailLabel = "Text cannot fit inside speech bubble";
      detailGuidance = "The text cannot fit inside the speech bubble shape without overflowing. Shorten the text or enlarge the bubble.";
    } else if (detailLower.includes("empty_ownership")) {
      detailLabel = "No space available in placement area";
      detailGuidance = "Surrounding elements leave no room for this text block. Reposition or shorten the text.";
    } else if (detailLower.includes("other_regions") || detailLower.includes("other_text") || detailLower.includes("collision")) {
      detailLabel = "Text collides with neighboring text";
      detailGuidance = "This region conflicts with nearby text. Move or resize neighboring boxes.";
    } else if (detailLower.includes("page_bounds")) {
      detailLabel = "Text extends outside page bounds";
      detailGuidance = "The text extends past the edges of the page. Move or resize the text block.";
    } else if (detail) {
      detailLabel = `No valid layout (${detail.replace(/_/g, " ")})`;
    }
    return { label: detailLabel, guidance: detailGuidance };
  }

  if (trimmed.startsWith("missing_glyph:")) {
    const codepoints = trimmed.slice("missing_glyph:".length).trim();
    return {
      label: `Missing font character(s): ${codepoints}`,
      guidance: "The selected font does not support these characters. Choose a font with broader language support or edit the text.",
    };
  }

  if (trimmed.startsWith("duplicate render ownership:")) {
    const ids = trimmed.slice("duplicate render ownership:".length).trim();
    return {
      label: `Duplicate region assignment (${ids})`,
      guidance: "Multiple text blocks were assigned to the same source region. Remove or merge duplicate blocks.",
    };
  }

  if (trimmed.startsWith("final layout collides with region")) {
    const regionId = trimmed.slice("final layout collides with region".length).trim();
    return {
      label: `Text collides with region ${regionId}`,
      guidance: "The rendered text overlaps with an adjacent text block. Reposition to prevent collision.",
    };
  }

  const label = trimmed
    .replace(/[:_]+/g, " ")
    .replace(/\b\w/g, (character) => character.toUpperCase());

  return {
    label,
    guidance: "Check the preserved original and either replace it with this translation or accept the original.",
  };
}
