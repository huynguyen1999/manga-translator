import type { Route } from "./+types/api";
import { proxyApiRequest } from "./api.server";

export async function loader({ request, params }: Route.LoaderArgs) {
  return proxyApiRequest(request, params["*"]);
}

export async function action({ request, params }: Route.ActionArgs) {
  return proxyApiRequest(request, params["*"]);
}
