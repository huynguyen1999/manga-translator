import type { Route } from "./+types/result";
import { handleResultGet, handleResultAction } from "./result.server";

export async function loader({ request, params }: Route.LoaderArgs) {
  return handleResultGet(request, params["*"]);
}

export async function action({ request, params }: Route.ActionArgs) {
  return handleResultAction(request, params["*"]);
}
