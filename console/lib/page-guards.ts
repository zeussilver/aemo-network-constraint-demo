import "server-only";
import { notFound } from "next/navigation";
import { NotFoundError } from "./services/types";

/** Use as `.catch(notFoundOnMissing)`: a missing record renders the 404 page. */
export function notFoundOnMissing(error: unknown): never {
  if (error instanceof NotFoundError) notFound();
  throw error;
}
