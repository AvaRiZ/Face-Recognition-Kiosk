function extractFilenameFromDisposition(disposition, fallback = "download") {
  if (!disposition) return fallback;

  const utf8Match = disposition.match(/filename\*\s*=\s*UTF-8''([^;]+)/i);
  if (utf8Match?.[1]) {
    try {
      return decodeURIComponent(utf8Match[1]).replace(/["]/g, "");
    } catch {
      return utf8Match[1].replace(/["]/g, "");
    }
  }

  const filenameMatch = disposition.match(/filename\s*=\s*"?([^";]+)"?/i);
  if (filenameMatch?.[1]) {
    return filenameMatch[1];
  }

  return fallback;
}

export async function downloadFile(url, fallbackFilename = "download") {
  const response = await fetch(url, {
    credentials: "include",
  });

  if (!response.ok) {
    if (response.status === 403 && typeof window !== "undefined" && window.location.pathname !== "/unauthorized") {
      window.location.href = "/unauthorized";
    }

    let message = "Download failed.";
    const isJson = response.headers.get("content-type")?.includes("application/json");

    if (isJson) {
      try {
        const data = await response.json();
        message = data?.message || message;
      } catch {
        // Ignore JSON parse failures and keep the default message.
      }
    }

    const error = new Error(message);
    error.status = response.status;
    throw error;
  }

  const blob = await response.blob();
  const disposition = response.headers.get("content-disposition");
  const filename = extractFilenameFromDisposition(disposition, fallbackFilename);
  const objectUrl = window.URL.createObjectURL(blob);
  const anchor = document.createElement("a");

  anchor.href = objectUrl;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  document.body.removeChild(anchor);
  window.URL.revokeObjectURL(objectUrl);

  return { filename };
}
