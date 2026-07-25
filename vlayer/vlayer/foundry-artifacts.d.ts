// Ambient declarations for the Foundry build artifacts that prove.ts imports.
// bun resolves `../out/<Name>.sol/<Name>` to the compiled JSON at runtime (the vlayer example pattern);
// these declarations just let `tsc --noEmit` type-check prove.ts without the artifacts on disk.
declare module "*/RegimeProver" {
  const spec: { abi: any; bytecode: { object: `0x${string}` } };
  export default spec;
}
declare module "*/RegimeVerifier" {
  const spec: { abi: any; bytecode: { object: `0x${string}` } };
  export default spec;
}
