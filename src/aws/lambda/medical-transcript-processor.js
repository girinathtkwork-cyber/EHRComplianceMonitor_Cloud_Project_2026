export const handler = async () => {
  throw new Error(
    "This legacy handler does not run transcript verification. Deploy stage_a_handler.lambda_handler instead."
  );
};
