{
  description = "{{PROJECT_NAME}} - cl9 project environment";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    flake-utils.url = "github:numtide/flake-utils";
  };

  outputs = { self, nixpkgs, flake-utils }:
    flake-utils.lib.eachDefaultSystem (system:
      let
        pkgs = nixpkgs.legacyPackages.${system};
      in
      {
        devShells.default = pkgs.mkShell {
          buildInputs = with pkgs; [
            # Add your tools here
            # Example:
            # git
            # jq
            # python3
          ];

          shellHook = ''
            echo "{{PROJECT_NAME}} environment loaded"
          '';
        };
      });
}
