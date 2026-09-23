import { redirect } from "react-router";

export function loader() {
  return redirect("/studio");
}

export default function LegacyPipelineLabRoute() {
  return null;
}
