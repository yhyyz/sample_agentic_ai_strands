/**
 * SECURITY NOTE: API keys should NEVER be exposed to the client.
 * This function is deprecated and should not be used in client-side code.
 * All API requests should go through server-side API routes that handle authentication.
 *
 * @deprecated Use server-side API routes with proper authentication instead
 */
export async function getApiKey(): Promise<string> {
  throw new Error(
    'SECURITY: Direct API key access is disabled. ' +
    'Use server-side API routes with proper authentication instead.'
  );
}

/**
 * Get auth headers for server-side API requests
 * All authentication is handled server-side via API routes
 *
 * @param userId - The user ID to include in headers
 * @returns Headers object with user ID for server-side authentication
 */
export const getAuthHeaders = async (userId: string) => {
  // Authentication is handled server-side by API routes
  // Client only needs to pass the user ID
  return {
    'X-User-ID': userId,
    'Content-Type': 'application/json'
  }
}
