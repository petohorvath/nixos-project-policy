/*
  Describe a flake's public outputs for `check`; see tools/outputs.py.

  Public outputs are every top-level output except `checks`, `devShells`, and
  `formatter`. Names stop at `<output>.<name>`, or `<output>.<system>.<name>`
  when every key of the output is a system name.

  Arguments:
  - flake: the result of `builtins.getFlake`.
  - system: the host system; only its per-system values are evaluated.
  - mode: "list" returns the public output names. "names" returns each
    output's names without evaluating values. "evaluate" also evaluates the
    values of `output`.
  - output: the output that "evaluate" describes.

  "names" and "evaluate" return `{ <output> = { names; empty; failed; }; }`,
  where `empty` lists empty namespaces and `failed` lists names whose value
  throws. Errors that `builtins.tryEval` cannot catch fail the whole call.
*/
{
  flake,
  system,
  mode,
  output,
}:
let
  inherit (builtins)
    all
    attrNames
    concatLists
    concatStringsSep
    elem
    filter
    isAttrs
    listToAttrs
    match
    seq
    tryEval
    ;

  private = [
    "checks"
    "devShells"
    "formatter"
  ];
  public = filter (name: !(elem name private)) (attrNames flake.outputs);

  join = concatStringsSep ".";
  none = {
    names = [ ];
    empty = [ ];
    failed = [ ];
  };
  merge = parts: {
    names = concatLists (map (part: part.names) parts);
    empty = concatLists (map (part: part.empty) parts);
    failed = concatLists (map (part: part.failed) parts);
  };
  succeeds = value: (tryEval (seq value true)).success;
  isSystem =
    name:
    match "[a-z0-9_]+-(linux|darwin|freebsd|netbsd|openbsd|cygwin|windows|none|wasi)" name != null;

  # Derivations, apps, and configurations are values, not namespaces.
  isNamespace =
    value:
    let
      result = tryEval (
        isAttrs value
        && !elem (value.type or null) [
          "derivation"
          "app"
        ]
        && !(value ? _type)
      );
    in
    result.success && result.value;

  # Force the part of a value that consumers use; other values stay lazy.
  forceValue =
    value:
    if !isAttrs value then
      value
    else if (value.type or null) == "derivation" then
      value.drvPath
    else if (value.type or null) == "app" then
      value.program
    else if (value._type or null) == "configuration" && value ? config.system.build.toplevel then
      value.config.system.build.toplevel.drvPath
    else
      value;

  describeValue =
    path: value: evaluate:
    none
    // {
      names = [ (join path) ];
      failed = if evaluate && !succeeds (forceValue value) then [ (join path) ] else [ ];
    };

  describeNamespace =
    path: value: describeChild:
    let
      names = attrNames value;
    in
    if names == [ ] then
      none // { empty = [ (join path) ]; }
    else
      merge (map (name: describeChild (path ++ [ name ]) value.${name}) names);

  describeSystem =
    path: value: evaluate:
    if !succeeds value then
      # Another system's check reports its own failures.
      if evaluate then none // { failed = [ (join path) ]; } else none
    else if isNamespace value then
      describeNamespace path value (childPath: child: describeValue childPath child evaluate)
    else
      describeValue path value evaluate;

  describeOutput =
    name: evaluate:
    let
      value = flake.outputs.${name};
      path = [ name ];
      keys = attrNames value;
      # nix flake check does not evaluate legacyPackages either; it can hold a
      # whole package set.
      evaluateValues = evaluate && name != "legacyPackages";
    in
    if !succeeds value then
      none // { failed = [ name ]; }
    else if !isNamespace value then
      describeValue path value evaluateValues
    else if keys != [ ] && all isSystem keys then
      merge (
        map (key: describeSystem (path ++ [ key ]) value.${key} (evaluateValues && key == system)) keys
      )
    else
      describeNamespace path value (childPath: child: describeValue childPath child evaluateValues);

  describe =
    names: evaluate:
    listToAttrs (
      map (name: {
        inherit name;
        value = describeOutput name evaluate;
      }) names
    );
in
if mode == "list" then
  public
else if mode == "names" then
  describe public false
else
  describe [ output ] true
