import { NextResponse } from "next/server";
import { listModels } from "@/lib/agents";
import { getSessionUserId } from "@/lib/auth";

export async function GET() {
  if (!(await getSessionUserId())) {
    return NextResponse.json({ error: "unauthorized" }, { status: 401 });
  }
  return NextResponse.json({ models: await listModels() });
}
