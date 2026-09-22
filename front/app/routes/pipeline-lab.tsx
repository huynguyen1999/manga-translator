import { PipelineLab } from "../components/PipelineLab";

export function meta() {
  return [
    { title: "Pipeline Lab · MangaStudio" },
    { name: "description", content: "Inspect every stage of one manga translation pipeline run." },
  ];
}

export default function PipelineLabRoute() {
  return <PipelineLab />;
}
