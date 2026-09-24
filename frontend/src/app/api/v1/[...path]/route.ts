import { NextRequest } from "next/server";

const backendOrigin = process.env.BACKEND_URL ?? "http://127.0.0.1:8000";

async function proxy(request: NextRequest, context: { params: Promise<{ path: string[] }> }) {
  const { path } = await context.params;
  const target = new URL(`/api/v1/${path.join("/")}`, backendOrigin);
  target.search = request.nextUrl.search;

  const headers = new Headers();
  const contentType = request.headers.get("content-type");
  if (contentType) headers.set("content-type", contentType);
  const collectionKey = request.headers.get("x-collection-key");
  if (collectionKey) headers.set("x-collection-key", collectionKey);
  const cookie = request.headers.get("cookie");
  if (cookie) headers.set("cookie", cookie);
  headers.set(
    "x-forwarded-proto",
    request.headers.get("x-forwarded-proto") ?? request.nextUrl.protocol.replace(":", ""),
  );
  const forwardedFor = request.headers.get("x-forwarded-for");
  if (forwardedFor) headers.set("x-forwarded-for", forwardedFor);

  const response = await fetch(target, {
    method: request.method,
    headers,
    body: request.method === "GET" || request.method === "HEAD" ? undefined : await request.arrayBuffer(),
    cache: "no-store",
  });

  const responseHeaders = new Headers();
  const responseType = response.headers.get("content-type");
  if (responseType) responseHeaders.set("content-type", responseType);
  for (const header of ["content-disposition", "content-length", "cache-control", "x-content-type-options", "set-cookie"]) {
    const value = response.headers.get(header);
    if (value) responseHeaders.set(header, value);
  }
  return new Response(response.body, { status: response.status, headers: responseHeaders });
}

export const GET = proxy;
export const POST = proxy;
export const PUT = proxy;
export const PATCH = proxy;
export const DELETE = proxy;
