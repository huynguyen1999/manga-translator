import type { EditableTextBlock } from "@/types";
import { apiUrl } from "@/utils/api";

export async function saveEditorEdits(
  folder: string,
  blocks: EditableTextBlock[],
  finalImageBase64: string,
  fetcher: typeof fetch = fetch,
) {
  const response = await fetcher(apiUrl(`/api/result/${folder}/save_edits`), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      text_regions: blocks,
      final_image_base64: finalImageBase64,
    }),
  });
  if (!response.ok) throw new Error(`Server returned ${response.status}`);
  return response.json().catch(() => ({}));
}
