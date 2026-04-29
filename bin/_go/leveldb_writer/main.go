// leveldb_writer — apply a JSON-described batch of put/delete operations to a
// LevelDB directory. Used by helium-sync's Python apply() path because there
// is no working Python LevelDB writer on Apple Silicon Homebrew (its leveldb
// dylib hides all C++ symbols, breaking plyvel).
//
// Input on stdin or via -ops <file>: a JSON array of operations. Each op is
//   {"op": "put",    "key": "string", "val_hex": "deadbeef"}
//   {"op": "delete", "key": "string"}
//
// Values are hex-encoded so the JSON channel stays text-clean even for
// arbitrary protobuf bytes.
//
// All operations are written in a single atomic batch via leveldb.DB.Write,
// so either everything lands or nothing does.

package main

import (
	"encoding/hex"
	"encoding/json"
	"flag"
	"fmt"
	"io"
	"os"

	"github.com/syndtr/goleveldb/leveldb"
	"github.com/syndtr/goleveldb/leveldb/opt"
)

type Op struct {
	Op     string `json:"op"`
	Key    string `json:"key"`
	ValHex string `json:"val_hex"`
}

func die(format string, args ...any) {
	fmt.Fprintf(os.Stderr, "leveldb_writer: "+format+"\n", args...)
	os.Exit(1)
}

func main() {
	dbPath := flag.String("db", "", "path to the LevelDB directory")
	opsFile := flag.String("ops", "", "path to JSON ops file (default: stdin)")
	flag.Parse()

	if *dbPath == "" {
		die("usage: leveldb_writer -db PATH [-ops FILE]")
	}

	var raw []byte
	var err error
	if *opsFile == "" {
		raw, err = io.ReadAll(os.Stdin)
	} else {
		raw, err = os.ReadFile(*opsFile)
	}
	if err != nil {
		die("reading ops: %v", err)
	}

	var ops []Op
	if err := json.Unmarshal(raw, &ops); err != nil {
		die("parsing ops JSON: %v", err)
	}

	// ErrorIfMissing=true so we never accidentally create an empty database
	// somewhere we didn't intend; if the path doesn't already contain a
	// LevelDB, the caller has the path wrong.
	db, err := leveldb.OpenFile(*dbPath, &opt.Options{ErrorIfMissing: true})
	if err != nil {
		die("opening %q: %v", *dbPath, err)
	}
	defer db.Close()

	batch := new(leveldb.Batch)
	puts, dels := 0, 0
	for i, op := range ops {
		switch op.Op {
		case "put":
			val, err := hex.DecodeString(op.ValHex)
			if err != nil {
				die("op[%d] bad hex: %v", i, err)
			}
			batch.Put([]byte(op.Key), val)
			puts++
		case "delete":
			batch.Delete([]byte(op.Key))
			dels++
		default:
			die("op[%d] unknown op %q (want put or delete)", i, op.Op)
		}
	}

	if err := db.Write(batch, &opt.WriteOptions{Sync: true}); err != nil {
		die("writing batch: %v", err)
	}

	fmt.Printf("ok: %d puts, %d deletes\n", puts, dels)
}
